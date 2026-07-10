from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_all_users, get_active_users
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
    if not message.text:
        await message.answer(
            "⚠️ Пожалуйста, отправьте <b>текстовое</b> сообщение.\n"
            "Фото, стикеры и файлы не поддерживаются в рассылке.",
            parse_mode="HTML"
        )
        return
    broadcast_text = message.text
    preview = (
        f"📢 Предпросмотр рассылки:\n"
        f"─────────────────────────────\n"
        f"{broadcast_text}"
    )
    try:
        await message.answer(
            preview,
            reply_markup=get_broadcast_confirm_keyboard(),
            parse_mode="HTML"
        )
        await state.update_data(broadcast_text=broadcast_text)
        await state.set_state(AdminStates.confirming_broadcast)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            await message.answer(
                "❌ <b>Ошибка HTML-разметки!</b>\n"
                "Бот не может распарсить ваше сообщение. Проверьте синтаксис:\n"
                "• Убедитесь, что все теги (<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>) закрыты корректно.\n"
                "• Если вы используете знаки `<` или `>`, экранируйте их как `&lt;` и `&gt;`.\n"
                "Введите текст сообщения заново:",
                parse_mode="HTML"
            )
        else:
            await message.answer(f"❌ Ошибка Telegram API при валидации: {e}\nВведите текст заново:")


@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return
    session = await get_session()
    try:
        users = await get_all_users(session)
        user_ids = [user.telegram_id for user in users]
        total_count = len(user_ids)
    finally:
        await session.close()
    success_count = 0
    fail_count = 0
    await callback.message.edit_text(f"⏳ Начинаю рассылку для {total_count} пользователей...")
    for uid in user_ids:
        try:
            await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            logging.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        except TelegramForbiddenError:
            fail_count += 1
            logging.info(f"User {uid} blocked the bot")
        except Exception as e:
            fail_count += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")
    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n"
        f"📤 Отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"👥 Всего: {total_count}",
        reply_markup=get_back_button("admin_menu")
    )
    logging.info(
        f"Admin {callback.from_user.id} broadcast to all: "
        f"success={success_count}, fail={fail_count}"
    )
    session = await get_session()
    try:
        await AuditService.log_action(
            session, callback.from_user.id, "BROADCAST",
            details=f"to all: {success_count} success, {fail_count} fail"
        )
    finally:
        await session.close()
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return
    session = await get_session()
    try:
        users = await get_active_users(session)
        user_ids = [user.telegram_id for user in users]
        total_count = len(user_ids)
    finally:
        await session.close()
    success_count = 0
    fail_count = 0
    await callback.message.edit_text(f"⏳ Начинаю рассылку для {total_count} активных пользователей...")
    for uid in user_ids:
        try:
            await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            logging.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                await callback.bot.send_message(uid, broadcast_text, parse_mode="HTML")
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        except TelegramForbiddenError:
            fail_count += 1
            logging.info(f"User {uid} blocked the bot")
        except Exception as e:
            fail_count += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")
    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n"
        f"📤 Отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"👥 Активных: {total_count}",
        reply_markup=get_back_button("admin_menu")
    )
    logging.info(
        f"Admin {callback.from_user.id} broadcast to active: "
        f"success={success_count}, fail={fail_count}"
    )
    session = await get_session()
    try:
        await AuditService.log_action(
            session, callback.from_user.id, "BROADCAST",
            details=f"to active: {success_count} success, {fail_count} fail"
        )
    finally:
        await session.close()
    await state.clear()
    await callback.answer()