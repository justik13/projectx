from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_all_users, get_active_users, mark_user_bot_blocked
from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.states import AdminStates
from config.settings import get_settings
import logging
import asyncio
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from services.audit_service import AuditService

router = Router()


def is_admin(telegram_id: int) -> bool:
    settings = get_settings()
    return telegram_id in settings.ADMIN_IDS


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🛠 Админка › 📢 <b>Рассылка</b>\n\n"
        "📢 Введите текст сообщения для рассылки:\n"
        "Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)",
        reply_markup=get_back_button("admin_menu")
    )
    await state.set_state(AdminStates.entering_broadcast_message)
    await callback.answer()


@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
        
    # 🔥 ИЗ ВАРИАНТА 2: Поддержка медиа (фото/документы)
    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await message.answer("⚠️ Отправьте текст или фото/документ с описанием.")
        return

    media_id = None
    content_type = message.content_type
    
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id

    preview = f"📢 <b>Предпросмотр рассылки ({content_type}):</b>\n\n{broadcast_text}"
    
    try:
        if media_id and content_type == "photo":
            await message.answer_photo(media_id, caption=preview, reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML")
        elif media_id and content_type == "document":
            await message.answer_document(media_id, caption=preview, reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML")
        else:
            await message.answer(preview, reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML")
            
        await state.update_data(broadcast_text=broadcast_text, media_id=media_id, content_type=content_type)
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await message.answer(f"❌ Ошибка валидации: {e}\nВведите заново:")


async def _send_broadcast_to_users(
    callback: CallbackQuery, 
    user_ids: list[int], 
    broadcast_text: str, 
    media_id: str | None,
    content_type: str,
    label: str
):
    """🔥 Вспомогательная функция для отправки рассылки с обработкой 403"""
    total_count = len(user_ids)
    success_count = 0
    fail_count = 0

    await callback.message.edit_text(f"⏳ Начинаю рассылку для {total_count} пользователей...")

    for uid in user_ids:
        try:
            # 🔥 ИСПРАВЛЕНО: переменные теперь приходят из аргументов
            if content_type == "photo" and media_id:
                await callback.bot.send_photo(uid, media_id, caption=broadcast_text, parse_mode="HTML")
            elif content_type == "document" and media_id:
                await callback.bot.send_document(uid, media_id, caption=broadcast_text, parse_mode="HTML")
            else:
                await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            logging.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                if content_type == "photo" and media_id:
                    await callback.bot.send_photo(uid, media_id, caption=broadcast_text, parse_mode="HTML")
                elif content_type == "document" and media_id:
                    await callback.bot.send_document(uid, media_id, caption=broadcast_text, parse_mode="HTML")
                else:
                    await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        except TelegramForbiddenError:
            fail_count += 1
            logging.info(f"User {uid} blocked the bot")
            try:
                session = await get_session()
                try:
                    await mark_user_bot_blocked(session, uid)
                finally:
                    await session.close()
            except Exception as e:
                logging.error(f"Failed to mark user {uid} as bot_blocked: {e}")
        except Exception as e:
            fail_count += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")

    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📤 Отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"👥 {label}: {total_count}",
        reply_markup=get_back_button("admin_menu")
    )
    logging.info(
        f"Admin {callback.from_user.id} broadcast to {label}: "
        f"success={success_count}, fail={fail_count}"
    )

    session = await get_session()
    try:
        await AuditService.log_action(
            session, callback.from_user.id, "BROADCAST",
            details=f"to {label}: {success_count} success, {fail_count} fail"
        )
    finally:
        await session.close()


@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    media_id = data.get("media_id")          # 🔥 Достаём из state
    content_type = data.get("content_type")  # 🔥 Достаём из state
    
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return

    session = await get_session()
    try:
        users = await get_all_users(session)
        user_ids = [user.telegram_id for user in users if not user.is_bot_blocked]
    finally:
        await session.close()

    await _send_broadcast_to_users(
        callback, user_ids, broadcast_text, 
        media_id, content_type, "Всего"  # 🔥 Передаём media_id и content_type
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    media_id = data.get("media_id")          # 🔥 Достаём из state
    content_type = data.get("content_type")  # 🔥 Достаём из state
    
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return

    session = await get_session()
    try:
        users = await get_active_users(session)
        user_ids = [user.telegram_id for user in users]
    finally:
        await session.close()

    await _send_broadcast_to_users(
        callback, user_ids, broadcast_text, 
        media_id, content_type, "Активных"  # 🔥 Передаём media_id и content_type
    )
    await state.clear()
    await callback.answer()