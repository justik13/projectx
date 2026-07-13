import asyncio
import logging
from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.keyboards.admin.broadcast import get_broadcast_result_keyboard, get_broadcast_close_keyboard
from bot.states import AdminStates
from bot import texts
from database.connection import session_scope
from database.repositories.users_repo import get_active_users, get_all_users, mark_user_bot_blocked
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import render_hub, send_hub_photo

router = Router()
logger = logging.getLogger(__name__)
_broadcast_stop_event = asyncio.Event()

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text(
            texts.BROADCAST_PROMPT, reply_markup=get_back_button("admin_menu"),
        )
    except Exception:
        pass
    await state.set_state(AdminStates.entering_broadcast_message)

@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await render_hub(message.bot, message.chat.id, texts.ERROR_TEXT_OR_MEDIA, get_back_button("admin_menu"))
        return
    media_id = None
    content_type = message.content_type
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id
        
    preview = texts.BROADCAST_PREVIEW.format(content_type=content_type, text=broadcast_text)
    try:
        if media_id and content_type == "photo":
            await send_hub_photo(
                message.bot, message.chat.id, message.photo[-1],
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        elif media_id and content_type == "document":
            from utils.telegram import send_hub_document
            await send_hub_document(
                message.bot, message.chat.id, message.document,
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        else:
            await render_hub(
                message.bot, message.chat.id, preview,
                get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        await state.update_data(
            broadcast_text=broadcast_text, media_id=media_id, content_type=content_type,
        )
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await render_hub(message.bot, message.chat.id, texts.ERROR_VALIDATION.format(error=e), get_back_button("admin_menu"))

async def _send_broadcast_to_users(bot, user_ids, broadcast_text, media_id, content_type, label, admin_id):
    _broadcast_stop_event.clear()
    total_count = len(user_ids)
    success_count = 0
    fail_count = 0
    for uid in user_ids:
        if _broadcast_stop_event.is_set():
            break
        try:
            await _dispatch_message(bot, uid, broadcast_text, media_id, content_type)
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            await asyncio.sleep(e.retry_after + 1)
            try:
                await _dispatch_message(bot, uid, broadcast_text, media_id, content_type)
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        except TelegramForbiddenError:
            fail_count += 1
            try:
                async with session_scope() as session:
                    await mark_user_bot_blocked(session, uid)
            except Exception:
                pass
        except Exception:
            fail_count += 1
            
    try:
        await bot.send_message(
            admin_id,
            texts.BROADCAST_RESULT.format(
                success_count=success_count, fail_count=fail_count,
                label=label, total_count=total_count,
            ),
            reply_markup=get_broadcast_result_keyboard(), parse_mode="HTML",
        )
    except Exception:
        pass

    try:
        async with session_scope() as session:
            await AuditService.log_action(
                session, admin_id, "BROADCAST",
                details=f"to {label}: {success_count} success, {fail_count} fail",
            )
    except Exception:
        pass

async def _dispatch_message(bot, uid, text, media_id, content_type):
    """🔥 ИСПРАВЛЕНО: Добавлена кнопка 'Прочитано' для очистки чата у пользователей"""
    kb = get_broadcast_close_keyboard()
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    await callback.answer("🚀 Рассылка запущена в фоне")
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(texts.ERROR_TEXT_EMPTY, show_alert=True)
        await state.clear()
        return
    media_id = data.get("media_id")
    content_type = data.get("content_type")
    users = await get_all_users(session)
    user_ids = [u.telegram_id for u in users if not u.is_bot_blocked]
    
    asyncio.create_task(_send_broadcast_to_users(
        callback.bot, user_ids, broadcast_text, media_id, content_type, "Всего", callback.from_user.id,
    ))
    try:
        await callback.message.edit_text(
            f"🚀 <b>Рассылка запущена!</b>\nОтправляю {len(user_ids)} пользователям...\nРезультат придёт отдельным сообщением.",
            reply_markup=get_back_button("admin_menu"), parse_mode="HTML",
        )
    except Exception:
        pass
    await state.clear()

@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    await callback.answer("🚀 Рассылка запущена в фоне")
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(texts.ERROR_TEXT_EMPTY, show_alert=True)
        await state.clear()
        return
    media_id = data.get("media_id")
    content_type = data.get("content_type")
    users = await get_active_users(session)
    user_ids = [u.telegram_id for u in users]
    
    asyncio.create_task(_send_broadcast_to_users(
        callback.bot, user_ids, broadcast_text, media_id, content_type, "Активных", callback.from_user.id,
    ))
    try:
        await callback.message.edit_text(
            f"🚀 <b>Рассылка запущена!</b>\nОтправляю {len(user_ids)} активным пользователям...\nРезультат придёт отдельным сообщением.",
            reply_markup=get_back_button("admin_menu"), parse_mode="HTML",
        )
    except Exception:
        pass
    await state.clear()

@router.callback_query(F.data == "broadcast_stop")
async def stop_broadcast(callback: CallbackQuery):
    await callback.answer("⏹ Рассылка остановлена", show_alert=True)
    _broadcast_stop_event.set()

@router.callback_query(F.data == "broadcast_dismiss")
async def dismiss_broadcast_result(callback: CallbackQuery):
    """Убирает уведомление о результате рассылки у АДМИНА"""
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

# 🔥 НОВЫЙ ХЕНДЛЕР: Убирает сообщение рассылки у ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ
@router.callback_query(F.data == "dismiss_broadcast")
async def dismiss_broadcast_message(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass