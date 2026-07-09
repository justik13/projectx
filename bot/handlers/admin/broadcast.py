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
# ✅ ИСПРАВЛЕНО: TelegramForbiddenError вместо TelegramForbiddenRequest
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest

router = Router()


def is_admin(telegram_id: int) -> bool:
    settings = get_settings()
    return telegram_id in settings.ADMIN_IDS


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    """Начать создание рассылки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📢 Введите текст сообщения для рассылки:\n\n"
        "Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)",
        reply_markup=get_back_button("admin_menu")
    )
    await state.set_state(AdminStates.entering_broadcast_message)
    await callback.answer()


@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    """Обработать введённое сообщение для рассылки с валидацией HTML"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    broadcast_text = message.text
    
    # Показываем предпросмотр и подтверждение
    preview = f"📢 Предпросмотр рассылки:\n"
    preview += "─────────────────────────────\n\n"
    preview += broadcast_text
    
    try:
        # Пробная отправка админу. Если HTML сломан — Telegram API выбросит исключение здесь
        await message.answer(
            preview,
            reply_markup=get_broadcast_confirm_keyboard(),
            parse_mode="HTML"
        )
        # Если отправка успешна — сохраняем валидный текст в состояние
        await state.update_data(broadcast_text=broadcast_text)
        await state.set_state(AdminStates.confirming_broadcast)
        
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower():
            await message.answer(
                "❌ <b>Ошибка HTML-разметки!</b>\n\n"
                "Бот не может распарсить ваше сообщение. Проверьте синтаксис:\n"
                "• Убедитесь, что все теги (<code>&lt;b&gt;</code>, <code>&lt;i&gt;</code>, <code>&lt;code&gt;</code>) закрыты корректно.\n"
                "• Если вы используете знаки `<` или `>`, экранируйте их как `&lt;` и `&gt;`.\n\n"
                "Введите текст сообщения заново:",
                parse_mode="HTML"
            )
        else:
            await message.answer(f"❌ Ошибка Telegram API при валидации: {e}\n\nВведите текст заново:")


@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext):
    """Отправить рассылку всем пользователям"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return
    
    # 1. БЫСТРО получаем ID пользователей и сразу закрываем сессию БД (Защита от Database Lock)
    session = await get_session()
    try:
        users = await get_all_users(session)
        user_ids = [user.telegram_id for user in users]  # Сохраняем только ID в RAM
        total_count = len(user_ids)
    finally:
        await session.close()  # Сессия закрыта, база полностью свободна для воркеров и оплат

    # 2. Долгий цикл отправки (БД больше не удерживается)
    success_count = 0
    fail_count = 0
    
    await callback.message.edit_text(f"⏳ Начинаю рассылку для {total_count} пользователей...")
    
    for uid in user_ids:
        try:
            await callback.bot.send_message(
                uid,
                broadcast_text,
                parse_mode="HTML"
            )
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            logging.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            # Повторная попытка после ожидания
            try:
                await callback.bot.send_message(
                    uid,
                    broadcast_text,
                    parse_mode="HTML"
                )
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        # ✅ ИСПРАВЛЕНО: TelegramForbiddenError
        except TelegramForbiddenError:
            fail_count += 1
            logging.info(f"User {uid} blocked the bot")
        except Exception as e:
            fail_count += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")
    
    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📤 Отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"👥 Всего: {total_count}",
        reply_markup=get_back_button("admin_menu")
    )
    
    logging.info(f"Admin {callback.from_user.id} broadcast to all: success={success_count}, fail={fail_count}")
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext):
    """Отправить рассылку только активным пользователям"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    
    if not broadcast_text:
        await callback.answer("❌ Текст сообщения пуст", show_alert=True)
        await state.clear()
        return
    
    # 1. БЫСТРО получаем ID активных пользователей и сразу закрываем сессию БД
    session = await get_session()
    try:
        users = await get_active_users(session)
        user_ids = [user.telegram_id for user in users]  # Сохраняем только ID в RAM
        total_count = len(user_ids)
    finally:
        await session.close()  # Сессия закрыта, база полностью свободна для воркеров и оплат
        
    # 2. Долгий цикл отправки (БД больше не удерживается)
    success_count = 0
    fail_count = 0
    
    await callback.message.edit_text(f"⏳ Начинаю рассылку для {total_count} активных пользователей...")
    
    for uid in user_ids:
        try:
            await callback.bot.send_message(
                uid,
                broadcast_text,
                parse_mode="HTML"
            )
            success_count += 1
            await asyncio.sleep(0.04)
        except TelegramRetryAfter as e:
            fail_count += 1
            logging.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            # Повторная попытка после ожидания
            try:
                await callback.bot.send_message(
                    uid,
                    broadcast_text,
                    parse_mode="HTML"
                )
                success_count += 1
                fail_count -= 1
            except Exception:
                pass
        # ✅ ИСПРАВЛЕНО: TelegramForbiddenError
        except TelegramForbiddenError:
            fail_count += 1
            logging.info(f"User {uid} blocked the bot")
        except Exception as e:
            fail_count += 1
            logging.warning(f"Failed to send broadcast to {uid}: {e}")
    
    await callback.message.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📤 Отправлено: {success_count}\n"
        f"❌ Ошибок: {fail_count}\n"
        f"👥 Активных: {total_count}",
        reply_markup=get_back_button("admin_menu")
    )
    
    logging.info(f"Admin {callback.from_user.id} broadcast to active: success={success_count}, fail={fail_count}")
    await state.clear()
    await callback.answer()