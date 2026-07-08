from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id, count_users, count_active_subscriptions, count_new_users_24h
from database.repositories.servers_repo import get_active_servers, get_total_free_ips
from bot.keyboards import get_admin_menu
from config.settings import get_settings
import logging

router = Router()


@router.message(F.text == "🛠 Админка")
async def show_admin(message: Message):
    """Показать дашборд админки"""
    settings = get_settings()
    telegram_id = message.from_user.id
    
    if telegram_id not in settings.ADMIN_IDS:
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return
    
    session = await get_session()
    try:
        # Получаем статистику
        total_users = await count_users(session)
        active_subs = await count_active_subscriptions(session)
        new_users_24h = await count_new_users_24h(session)
        free_ips = await get_total_free_ips(session)
        
        text = f"""🛠 Админ-панель
─────────────────────────────

📊 Статистика:

👥 Всего пользователей: {total_users}
✅ Активных подписок: {active_subs}
🆕 Новых за 24ч: {new_users_24h}
🌍 Свободных IP: {free_ips}
"""
        
        await message.answer(
            text,
            reply_markup=get_admin_menu()
        )
    finally:
        await session.close()


@router.callback_query(F.data == "admin_menu")
async def back_to_admin(callback: CallbackQuery):
    """Вернуться в админку"""
    settings = get_settings()
    telegram_id = callback.from_user.id
    
    if telegram_id not in settings.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    session = await get_session()
    try:
        total_users = await count_users(session)
        active_subs = await count_active_subscriptions(session)
        new_users_24h = await count_new_users_24h(session)
        free_ips = await get_total_free_ips(session)
        
        text = f"""🛠 Админ-панель
─────────────────────────────

📊 Статистика:

👥 Всего пользователей: {total_users}
✅ Активных подписок: {active_subs}
🆕 Новых за 24ч: {new_users_24h}
🌍 Свободных IP: {free_ips}
"""
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_menu()
        )
        await callback.answer()
    finally:
        await session.close()