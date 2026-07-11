from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from database.repositories.users_repo import (
    get_user_count, get_active_subscriptions_count, get_new_users_count_24h
)
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.audit_repo import get_recent_audit_logs
from bot.keyboards import get_admin_menu, get_audit_keyboard
from config.settings import get_settings
from utils.formatters import format_datetime
import logging
from sqlalchemy.ext.asyncio import AsyncSession

router = Router()


@router.message(F.text == "🛠 Админка")
async def show_admin(message: Message, session: AsyncSession = None):
    try:
        await message.delete()
    except Exception:
        pass
    settings = get_settings()
    telegram_id = message.from_user.id
    if telegram_id not in settings.ADMIN_IDS:
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return
    total_users = await get_user_count(session)
    active_subs = await get_active_subscriptions_count(session)
    new_users_24h = await get_new_users_count_24h(session)
    free_ips = await get_total_free_ips(session)
    text = (
        f"🛠 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных подписок: {active_subs}\n"
        f"🆕 Новых за 24ч: {new_users_24h}\n"
        f"🌍 Свободных IP: {free_ips}\n"
    )
    await message.answer(
        text,
        reply_markup=get_admin_menu(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_menu")
async def back_to_admin(callback: CallbackQuery, session: AsyncSession = None):
    settings = get_settings()
    telegram_id = callback.from_user.id
    if telegram_id not in settings.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    total_users = await get_user_count(session)
    active_subs = await get_active_subscriptions_count(session)
    new_users_24h = await get_new_users_count_24h(session)
    free_ips = await get_total_free_ips(session)
    text = (
        f"🛠 <b>Админ-панель</b>\n\n"
        f"📊 <b>Статистика:</b>\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Активных подписок: {active_subs}\n"
        f"🆕 Новых за 24ч: {new_users_24h}\n"
        f"🌍 Свободных IP: {free_ips}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_admin_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_audit")
async def show_audit_log(callback: CallbackQuery, session: AsyncSession = None):
    settings = get_settings()
    if callback.from_user.id not in settings.ADMIN_IDS:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    logs = await get_recent_audit_logs(session, limit=10)
    text = (
        f"🛠 Админка › 📜 <b>Аудит-лог</b>\n\n"
        f"<i>Последние 10 действий администраторов:</i>\n\n"
    )
    if not logs:
        text += "_Лог действий пуст._"
    else:
        for log in logs:
            date = format_datetime(log.created_at)
            action_map = {
                "EXTEND": "⏰ Продлил",
                "BAN": "🚫 Забанил",
                "UNBAN": "✅ Разбанил",
                "DELETE_SERVER": "🗑 Удалил сервер",
                "ADD_SERVER": "➕ Добавил сервер",
                "TOGGLE_SERVER": "🔄 Переключил сервер",
                "DELETE_TARIFF": "🗑 Удалил тариф",
                "ADD_TARIFF": "➕ Добавил тариф",
                "EDIT_TARIFF": "✏️ Изменил тариф",
                "BROADCAST": "📢 Сделал рассылку",
            }
            action_text = action_map.get(log.action, log.action)
            target_info = ""
            if log.target_type and log.target_id:
                target_info = f" {log.target_type} <code>{log.target_id}</code>"
            details = f"\n<i>{log.details}</i>" if log.details else ""
            text += (
                f"[{date}]\n"
                f"Admin <code>{log.admin_id}</code>\n"
                f"➡️ {action_text}{target_info}{details}\n\n"
            )
    await callback.message.edit_text(
        text,
        reply_markup=get_audit_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()
