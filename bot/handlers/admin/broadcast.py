from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_all_users, get_active_users
from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.states import AdminStates
from config.settings import get_settings
import logging

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
    """Обработать введённое сообщение для рассылки"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    broadcast_text = message.text
    
    # Сохраняем текст в состоянии
    await state.update_data(broadcast_text=broadcast_text)
    
    # Показываем предпросмотр и подтверждение
    preview = f"📢 Предпросмотр рассылки:\n"
    preview += "─────────────────────────────\n\n"
    preview += broadcast_text
    
    await message.answer(
        preview,
        reply_markup=get_broadcast_confirm_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.confirming_broadcast)


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
    
    session = await get_session()
    try:
        users = await get_all_users(session)
        success_count = 0
        fail_count = 0
        
        for user in users:
            try:
                await callback.bot.send_message(
                    user.telegram_id,
                    broadcast_text,
                    parse_mode="HTML"
                )
                success_count += 1
            except Exception as e:
                fail_count += 1
                logging.warning(f"Failed to send broadcast to {user.telegram_id}: {e}")
        
        await callback.message.edit_text(
            f"✅ Рассылка завершена!\n\n"
            f"📤 Отправлено: {success_count}\n"
            f"❌ Ошибок: {fail_count}\n"
            f"👥 Всего: {len(users)}",
            reply_markup=get_back_button("admin_menu")
        )
        
        logging.info(f"Admin {callback.from_user.id} broadcast to all: success={success_count}, fail={fail_count}")
        await state.clear()
        await callback.answer()
    finally:
        await session.close()


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
    
    session = await get_session()
    try:
        users = await get_active_users(session)
        success_count = 0
        fail_count = 0
        
        for user in users:
            try:
                await callback.bot.send_message(
                    user.telegram_id,
                    broadcast_text,
                    parse_mode="HTML"
                )
                success_count += 1
            except Exception as e:
                fail_count += 1
                logging.warning(f"Failed to send broadcast to {user.telegram_id}: {e}")
        
        await callback.message.edit_text(
            f"✅ Рассылка завершена!\n\n"
            f"📤 Отправлено: {success_count}\n"
            f"❌ Ошибок: {fail_count}\n"
            f"👥 Активных: {len(users)}",
            reply_markup=get_back_button("admin_menu")
        )
        
        logging.info(f"Admin {callback.from_user.id} broadcast to active: success={success_count}, fail={fail_count}")
        await state.clear()
        await callback.answer()
    finally:
        await session.close()