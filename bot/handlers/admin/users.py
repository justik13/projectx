from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import (
    get_user_by_telegram_id, get_users_paginated, 
    get_users_count, update_user, get_user_referrals
)
from database.repositories.profiles_repo import get_user_profiles, update_profile
from database.repositories.servers_repo import get_server_by_id
from services.subscription import SubscriptionService
from bot.keyboards import (
    get_admin_users_keyboard, get_admin_user_card_keyboard,
    get_admin_extend_days_keyboard, get_back_button
)
from bot.states import AdminStates
from utils.formatters import format_datetime, format_days_left
from config.settings import get_settings
from datetime import datetime, timedelta, timezone
import logging
import math

from services.amnezia_client import AmneziaClient

router = Router()

USERS_PER_PAGE = 10


def is_admin(telegram_id: int) -> bool:
    settings = get_settings()
    return telegram_id in settings.ADMIN_IDS


@router.callback_query(F.data == "admin_users")
async def show_users_list(callback: CallbackQuery):
    """Показать список пользователей (первая страница)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    session = await get_session()
    try:
        total_users = await get_users_count(session)
        total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
        users = await get_users_paginated(session, page=1, per_page=USERS_PER_PAGE)
        
        text = await _build_users_list_text(users, 1, total_pages, total_users)
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_users_keyboard(page=1, total_pages=total_pages)
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(callback: CallbackQuery):
    """Пагинация списка пользователей"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    page = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        total_users = await get_users_count(session)
        total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
        users = await get_users_paginated(session, page=page, per_page=USERS_PER_PAGE)
        
        text = await _build_users_list_text(users, page, total_pages, total_users)
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_users_keyboard(page=page, total_pages=total_pages)
        )
        await callback.answer()
    finally:
        await session.close()


async def _build_users_list_text(users, page: int, total_pages: int, total: int) -> str:
    text = f"👥 Пользователи (стр. {page}/{total_pages})\n"
    text += f"Всего: {total}\n"
    text += "─────────────────────────────\n\n"
    
    if not users:
        text += "_Пользователей пока нет_"
        return text
    
    for user in users:
        status = "🟢" if user.subscription_end and user.subscription_end > datetime.utcnow() else "🔴"
        ban = "🚫" if user.is_banned else ""
        username = f"@{user.username}" if user.username else "—"
        days = format_days_left(user.subscription_end)
        
        text += f"{status}{ban} <b>{user.telegram_id}</b> {username}\n"
        text += f"   Осталось: {days}\n\n"
    
    return text


@router.callback_query(F.data == "admin_users_search")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    """Начать поиск пользователя по ID"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🔍 Введите Telegram ID пользователя:",
        reply_markup=get_back_button("admin_users")
    )
    await state.set_state(AdminStates.searching_user)
    await callback.answer()


@router.message(AdminStates.searching_user)
async def process_search_user(message: Message, state: FSMContext):
    """Обработать поиск пользователя"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID. Попробуйте ещё раз:")
        return
    
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await message.answer(
                f"❌ Пользователь с ID {telegram_id} не найден.",
                reply_markup=get_back_button("admin_users")
            )
            await state.clear()
            return
        
        await _show_user_card(message, user, session)
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(callback: CallbackQuery):
    """Показать карточку пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
        
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        await _show_user_card_edit(callback.message, user, session)
        await callback.answer()
    finally:
        await session.close()


async def _show_user_card(message: Message, user, session):
    """Показать карточку пользователя (новое сообщение)"""
    text = await _build_user_card_text(user, session)
    await message.answer(
        text,
        reply_markup=get_admin_user_card_keyboard(user.telegram_id),
        parse_mode="HTML"
    )


async def _show_user_card_edit(message, user, session):
    """Показать карточку пользователя (редактирование)"""
    text = await _build_user_card_text(user, session)
    await message.edit_text(
        text,
        reply_markup=get_admin_user_card_keyboard(user.telegram_id),
        parse_mode="HTML"
    )


async def _build_user_card_text(user, session) -> str:
    has_access = user.subscription_end and user.subscription_end > datetime.now(timezone.utc)
    status = "🟢 Активен" if has_access else "🔴 Неактивен"
    ban = "🚫 ЗАБАНЕН" if user.is_banned else "✅ Не забанен"
    
    profiles = await get_user_profiles(session, user.id)
    devices_count = len(profiles)
    
    text = f"👤 Карточка пользователя\n"
    text += "─────────────────────────────\n\n"
    text += f"<b>ID:</b> {user.telegram_id}\n"
    text += f"<b>Username:</b> @{user.username or '—'}\n"
    text += f"<b>Имя:</b> {user.first_name or '—'}\n\n"
    text += f"<b>Статус:</b> {status}\n"
    text += f"<b>Бан:</b> {ban}\n"
    text += f"<b>Действует до:</b> {format_datetime(user.subscription_end)}\n"
    text += f"<b>Осталось:</b> {format_days_left(user.subscription_end)}\n\n"
    text += f"<b>Устройств:</b> {devices_count}/{user.device_limit}\n"
    text += f"<b>Рефералов:</b> {len(await get_user_referrals(session, user.telegram_id)) if user.referral_days > 0 else 0}\n"
    text += f"<b>Бонусных дней:</b> +{user.referral_days}\n\n"
    text += f"<b>Регистрация:</b> {format_datetime(user.created_at)}"
    
    return text


@router.callback_query(F.data.startswith("admin_user_extend:"))
async def show_extend_options(callback: CallbackQuery):
    """Показать варианты продления подписки"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    
    await callback.message.edit_text(
        f"⏰ Выберите срок продления для пользователя {telegram_id}:",
        reply_markup=get_admin_extend_days_keyboard(telegram_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_extend_days:"))
async def extend_subscription(callback: CallbackQuery):
    """Продлить подписку на выбранный срок"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    
    session = await get_session()
    try:
        await SubscriptionService.extend_subscription(session, telegram_id, days)
        
        user = await get_user_by_telegram_id(session, telegram_id)
        days_text = "∞ навсегда" if days >= 36500 else f"{days} дней"
        
        await callback.answer(
            f"✅ Подписка продлена на {days_text}",
            show_alert=True
        )
        
        logging.info(
            f"Admin {callback.from_user.id} extended user {telegram_id} by {days} days"
        )
        
        # Возвращаемся к карточке
        await _show_user_card_edit(callback.message, user, session)
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_extend_custom:"))
async def start_custom_extend(callback: CallbackQuery, state: FSMContext):
    """Запросить количество дней вручную"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    
    await callback.message.edit_text(
        f"⌨️ Введите количество дней для продления пользователя {telegram_id}:",
        reply_markup=get_back_button(f"admin_user_extend:{telegram_id}")
    )
    await state.set_state(AdminStates.entering_custom_days)
    await state.update_data(target_user_id=telegram_id)
    await callback.answer()


@router.message(AdminStates.entering_custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    """Обработать введённое количество дней"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        days = int(message.text.strip())
        if days < 1 or days > 36500:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 1 до 36500. Попробуйте ещё раз:")
        return
    
    data = await state.get_data()
    telegram_id = data.get("target_user_id")
    
    session = await get_session()
    try:
        await SubscriptionService.extend_subscription(session, telegram_id, days)
        
        user = await get_user_by_telegram_id(session, telegram_id)
        
        await message.answer(
            f"✅ Подписка пользователя {telegram_id} продлена на {days} дней.\n"
            f"Действует до: {format_datetime(user.subscription_end)}",
            reply_markup=get_admin_user_card_keyboard(telegram_id),
            parse_mode="HTML"
        )
        
        logging.info(
            f"Admin {message.from_user.id} extended user {telegram_id} by {days} days (custom)"
        )
        
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_user_ban:"))
async def toggle_ban_user(callback: CallbackQuery):
    """Забанить/разбанить пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        new_status = not user.is_banned
        await update_user(session, user, is_banned=new_status)
        
        # Если пользователь банится - отключаем его профили на серверах
        if new_status:
            profiles = await get_user_profiles(session, user.id)
            for profile in profiles:
                server = await get_server_by_id(session, profile.server_id)
                if server and server.is_active:
                    try:
                        client = AmneziaClient(server.api_url, server.api_key)
                        await client.update_client(
                            client_id=profile.peer_id,
                            status="disabled"
                        )
                        await update_profile(session, profile, is_active=False)
                    except Exception as e:
                        logging.error(f"Failed to disable profile {profile.id} on server {server.id}: {e}")
        else:
            # Если пользователь разбанен - включаем его профили на серверах
            profiles = await get_user_profiles(session, user.id)
            for profile in profiles:
                server = await get_server_by_id(session, profile.server_id)
                if server:
                    try:
                        client = AmneziaClient(server.api_url, server.api_key)
                        await client.update_client(
                            client_id=profile.peer_id,
                            status="active"
                        )
                        await update_profile(session, profile, is_active=True)
                    except Exception as e:
                        logging.error(f"Failed to enable profile {profile.id} on server {server.id}: {e}")

        action = "забанен" if new_status else "разбанен"
        await callback.answer(f"✅ Пользователь {action}", show_alert=True)
        
        logging.info(
            f"Admin {callback.from_user.id} {action} user {telegram_id}"
        )
        
        # Обновляем карточку
        user = await get_user_by_telegram_id(session, telegram_id)
        await _show_user_card_edit(callback.message, user, session)
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_user_devices:"))
async def show_user_devices(callback: CallbackQuery):
    """Показать устройства пользователя"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    telegram_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        
        profiles = await get_user_profiles(session, user.id)
        
        text = f"🔧 Устройства пользователя {telegram_id}\n"
        text += "─────────────────────────────\n\n"
        
        if not profiles:
            text += "_Устройств нет_"
        else:
            for p in profiles:
                text += f"📱 <b>{p.device_name}</b>\n"
                text += f"   Peer: <code>{p.peer_id[:16]}...</code>\n"
                text += f"   Трафик: ↓{p.traffic_down} ↑{p.traffic_up}\n\n"
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="← К карточке", callback_data=f"admin_user_card:{telegram_id}")
        builder.adjust(1)
        
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()
