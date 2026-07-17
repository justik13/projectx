"""
Хендлеры рассылки.

🔥 ИСПРАВЛЕНО (Часть 2):
- BroadcastProgress модель в БД для resume при crash
- Global rate limiter для Telegram API
"""

import asyncio
import json
import logging
from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.keyboards.admin.broadcast import (
    get_broadcast_result_keyboard, get_broadcast_close_keyboard
)
from bot.states import AdminStates
from bot import texts
from bot.constants import BROADCAST_DELAY
from database.connection import session_scope
from database.models import BroadcastProgress
from database.repositories.users_repo import (
    get_active_users, get_all_users, mark_user_bot_blocked
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import render_hub, send_hub_photo

router = Router()
logger = logging.getLogger(__name__)

# Per-admin stop events
_broadcast_stop_events: dict[int, asyncio.Event] = {}
_broadcast_in_progress: set[int] = set()

# 🔥 ИСПРАВЛЕНО: Global rate limiter для broadcast
class BroadcastRateLimiter:
    """Token bucket rate limiter для рассылки (25 msg/sec)."""
    
    def __init__(self, rate: float = 25.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = asyncio.get_event_loop().time()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            elapsed = now - self.last_refill
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_refill = now
            
            if self.tokens < 1.0:
                await asyncio.sleep(1.0 / self.rate)
                return await self.acquire()
            
            self.tokens -= 1.0

_broadcast_limiter = BroadcastRateLimiter()


def _get_stop_event(admin_id: int) -> asyncio.Event:
    if admin_id not in _broadcast_stop_events:
        _broadcast_stop_events[admin_id] = asyncio.Event()
    return _broadcast_stop_events[admin_id]


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
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_TEXT_OR_MEDIA, get_back_button("admin_menu")
        )
        return
    
    media_id = None
    content_type = message.content_type
    
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id
    
    preview = texts.BROADCAST_PREVIEW.format(
        content_type=content_type, text=broadcast_text
    )
    
    try:
        if media_id and content_type == "photo":
            await send_hub_photo(
                message.bot, message.chat.id, message.photo[-1],
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        elif media_id and content_type == "document":
            from utils.telegram import send_hub_document
            await send_hub_document(
                message.bot, message.chat.id, message.document,
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        else:
            await render_hub(
                message.bot, message.chat.id, preview,
                get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        
        await state.update_data(
            broadcast_text=broadcast_text, media_id=media_id,
            content_type=content_type,
        )
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_VALIDATION.format(error=e),
            get_back_button("admin_menu")
        )


async def _send_with_html(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(
            uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb
        )
    elif content_type == "document" and media_id:
        await bot.send_document(
            uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb
        )
    else:
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)


async def _send_plain(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, reply_markup=kb)
    else:
        await bot.send_message(uid, text, reply_markup=kb)


async def _dispatch_message(bot, uid, text, media_id, content_type):
    """Отправляет одно сообщение рассылки с fallback на plain text."""
    kb = get_broadcast_close_keyboard()
    try:
        await _send_with_html(bot, uid, text, media_id, content_type, kb)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower() or "parse" in str(e).lower():
            logger.warning(
                f"HTML parse failed for user {uid}, falling back to plain text"
            )
            await _send_plain(bot, uid, text, media_id, content_type, kb)
        else:
            raise


async def _send_broadcast_to_users_with_resume(
    bot, progress_id: int
):
    """
    🔥 ИСПРАВЛЕНО (Часть 2): Возобновляемая рассылка с прогрессом в БД.
    
    При crash бота прогресс сохраняется в BroadcastProgress.
    При следующем запуске бот может продолжить с того же места.
    """
    stop_event = None
    
    async with session_scope() as session:
        progress = await session.get(BroadcastProgress, progress_id)
        if not progress or progress.status != "in_progress":
            return
        
        admin_id = progress.admin_id
        stop_event = _get_stop_event(admin_id)
        stop_event.clear()
        
        user_ids = json.loads(progress.user_ids_json)
        start_index = progress.last_processed_index
        total_count = progress.total_count
        
        logger.info(
            f"Broadcast resume: admin={admin_id}, progress_id={progress_id}, "
            f"starting from index {start_index}/{total_count}"
        )
    
    blocked_user_ids = []
    
    try:
        for i in range(start_index, len(user_ids)):
            if stop_event and stop_event.is_set():
                break
            
            uid = user_ids[i]
            
            try:
                # 🔥 ИСПРАВЛЕНО: Global rate limiter
                await _broadcast_limiter.acquire()
                await _dispatch_message(
                    bot, uid, progress.broadcast_text,
                    progress.media_id, progress.content_type
                )
                
                # Обновляем прогресс в БД каждые 50 сообщений
                if (i + 1) % 50 == 0:
                    async with session_scope() as session:
                        progress = await session.get(BroadcastProgress, progress_id)
                        if progress:
                            progress.last_processed_index = i + 1
                            progress.success_count += 50
                            await session.commit()
                
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                try:
                    await _broadcast_limiter.acquire()
                    await _dispatch_message(
                        bot, uid, progress.broadcast_text,
                        progress.media_id, progress.content_type
                    )
                except Exception:
                    async with session_scope() as session:
                        progress = await session.get(BroadcastProgress, progress_id)
                        if progress:
                            progress.fail_count += 1
                            progress.last_processed_index = i + 1
                            await session.commit()
            except TelegramForbiddenError:
                blocked_user_ids.append(uid)
                async with session_scope() as session:
                    progress = await session.get(BroadcastProgress, progress_id)
                    if progress:
                        progress.fail_count += 1
                        progress.last_processed_index = i + 1
                        await session.commit()
            except Exception as e:
                logger.error(f"Broadcast error for user {uid}: {e}")
                async with session_scope() as session:
                    progress = await session.get(BroadcastProgress, progress_id)
                    if progress:
                        progress.fail_count += 1
                        progress.last_processed_index = i + 1
                        await session.commit()
    finally:
        if stop_event:
            stop_event.clear()
        
        async with session_scope() as session:
            progress = await session.get(BroadcastProgress, progress_id)
            if progress:
                progress.status = "completed" if not (stop_event and stop_event.is_set()) else "stopped"
                await session.commit()
                
                final_progress = progress
        
        if blocked_user_ids:
            try:
                async with session_scope() as session:
                    for uid in blocked_user_ids:
                        await mark_user_bot_blocked(session, uid)
            except Exception as e:
                logger.error(f"Failed to batch mark users as bot_blocked: {e}")
        
        _broadcast_in_progress.discard(admin_id)
    
    try:
        await bot.send_message(
            admin_id,
            texts.BROADCAST_RESULT.format(
                success_count=final_progress.success_count,
                fail_count=final_progress.fail_count,
                label=final_progress.label,
                total_count=final_progress.total_count,
            ),
            reply_markup=get_broadcast_result_keyboard(), parse_mode="HTML",
        )
    except Exception:
        pass
    
    try:
        async with session_scope() as session:
            await AuditService.log_action(
                session, admin_id, "BROADCAST",
                details=(
                    f"to {final_progress.label}: {final_progress.success_count} success, "
                    f"{final_progress.fail_count} fail"
                ),
            )
    except Exception:
        pass


@router.callback_query(
    F.data == "broadcast_send_all", AdminStates.confirming_broadcast
)
async def broadcast_to_all(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession = None
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    
    admin_id = callback.from_user.id
    
    if admin_id in _broadcast_in_progress:
        await callback.answer(
            "⏳ Рассылка уже идёт, дождитесь завершения", show_alert=True
        )
        return
    
    await callback.answer("🚀 Рассылка запущена в фоне")
    
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
    
    # Создаём BroadcastProgress в БД
    progress_id = None
    async with session_scope() as session:
        progress = BroadcastProgress(
            admin_id=admin_id,
            total_count=len(user_ids),
            user_ids_json=json.dumps(user_ids),
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
            label="Всего",
            status="in_progress",
        )
        session.add(progress)
        await session.commit()
        await session.refresh(progress)
        progress_id = progress.id
    
    _broadcast_in_progress.add(admin_id)
    
    asyncio.create_task(_send_broadcast_to_users_with_resume(
        callback.bot, progress_id
    ))
    
    try:
        await callback.message.edit_text(
            f"🚀 <b>Рассылка запущена!</b>\n"
            f"Отправляю {len(user_ids)} пользователям...\n"
            f"Результат придёт отдельным сообщением.",
            reply_markup=get_back_button("admin_menu"), parse_mode="HTML",
        )
    except Exception:
        pass
    
    await state.clear()


@router.callback_query(
    F.data == "broadcast_send_active", AdminStates.confirming_broadcast
)
async def broadcast_to_active(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession = None
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    
    admin_id = callback.from_user.id
    
    if admin_id in _broadcast_in_progress:
        await callback.answer(
            "⏳ Рассылка уже идёт, дождитесь завершения", show_alert=True
        )
        return
    
    await callback.answer("🚀 Рассылка запущена в фоне")
    
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
    
    progress_id = None
    async with session_scope() as session:
        progress = BroadcastProgress(
            admin_id=admin_id,
            total_count=len(user_ids),
            user_ids_json=json.dumps(user_ids),
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
            label="Активных",
            status="in_progress",
        )
        session.add(progress)
        await session.commit()
        await session.refresh(progress)
        progress_id = progress.id
    
    _broadcast_in_progress.add(admin_id)
    
    asyncio.create_task(_send_broadcast_to_users_with_resume(
        callback.bot, progress_id
    ))
    
    try:
        await callback.message.edit_text(
            f"🚀 <b>Рассылка запущена!</b>\n"
            f"Отправляю {len(user_ids)} активным пользователям...\n"
            f"Результат придёт отдельным сообщением.",
            reply_markup=get_back_button("admin_menu"), parse_mode="HTML",
        )
    except Exception:
        pass
    
    await state.clear()


@router.callback_query(F.data == "broadcast_stop")
async def stop_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    
    admin_id = callback.from_user.id
    stop_event = _get_stop_event(admin_id)
    stop_event.set()
    
    await callback.answer("⏹ Рассылка остановлена", show_alert=True)


@router.callback_query(F.data == "broadcast_dismiss")
async def dismiss_broadcast_result(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "dismiss_broadcast")
async def dismiss_broadcast_message(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass
