from sqlalchemy import select, update
from database.models import Server, VPNProfile
import html
import logging
import math
import asyncio
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from database.connection import get_session
from database.repositories.users_repo import (
    get_user_by_telegram_id, get_users_paginated, get_user_count, update_user, get_user_referrals)
from database.repositories.profiles_repo import get_user_profiles
from services.subscription import SubscriptionService
from bot.keyboards import (
    get_admin_users_keyboard, get_admin_user_card_keyboard, get_admin_extend_days_keyboard, get_back_button)
from bot.states import AdminStates
from utils.formatters import format_datetime, format_days_left
from config.settings import get_settings
from services.amnezia_client import AmneziaClient

router = Router()
logger = logging.getLogger(__name__)
USERS_PER_PAGE = 10

def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS

@router.callback_query(F.data == "admin_users")
async def show_users_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    session = await get_session()
    try:
        total_users = await get_user_count(session)
        total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
        users = await get_users_paginated(session, page=1, per_page=USERS_PER_PAGE)
        text = await _build_users_list_text(users, 1, total_pages, total_users)
        await callback.message.edit_text(text, reply_markup=get_admin_users_keyboard(page=1, total_pages=total_pages),
                                          parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()

@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        total_users = await get_user_count(session)
        total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
        users = await get_users_paginated(session, page=page, per_page=USERS_PER_PAGE)
        text = await _build_users_list_text(users, page, total_pages, total_users)
        await callback.message.edit_text(text, reply_markup=get_admin_users_keyboard(page=page, total_pages=total_pages),
                                          parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()

async def _build_users_list_text(users, page: int, total_pages: int, total: int) -> str:
    text = f"👥 Пользователи (стр. {page}/{total_pages})\nВсего: {total}\n─────────────────────────────\n"
    if not users:
        text += "_Пользователей пока нет_\n"
        return text
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for user in users:
        status = "🟢" if user.subscription_end and user.subscription_end > now else "🔴"
        ban = "🚫" if user.is_banned else ""
        username = f"@{html.escape(user.username)}" if user.username else "—"
        days = format_days_left(user.subscription_end)
        text += f"{status}{ban} <b>{user.telegram_id}</b> {username}\n    Осталось: {days}\n"
    return text

@router.callback_query(F.data == "admin_users_search")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🔍 Введите Telegram ID пользователя:",
                                      reply_markup=get_back_button("admin_users"))
    await state.set_state(AdminStates.searching_user)
    await callback.answer()

@router.message(AdminStates.searching_user)
async def process_search_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    # 🔥 P1 FIX
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод. Отправьте числовой Telegram ID:")
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer("⚠️ Операция прервана командой.")
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await message.answer(f"❌ Пользователь с ID {telegram_id} не найден.",
                                  reply_markup=get_back_button("admin_users"))
            await state.clear()
            return
        await _show_user_card(message, user, session)
        await state.clear()
    finally:
        await session.close()

@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()  # 🔥 P1 FIX: Сбрасываем Ghost State
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
    text = await _build_user_card_text(user, session)
    await message.answer(text, reply_markup=get_admin_user_card_keyboard(user.telegram_id), parse_mode="HTML")

async def _show_user_card_edit(message, user, session):
    text = await _build_user_card_text(user, session)
    try:
        await message.edit_text(text, reply_markup=get_admin_user_card_keyboard(user.telegram_id), parse_mode="HTML")
    except TelegramBadRequest:
        pass

async def _build_user_card_text(user, session) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    has_access = user.subscription_end and user.subscription_end > now
    status = "🟢 Активен" if has_access else "🔴 Неактивен"
    ban = "🚫 ЗАБАНЕН" if user.is_banned else "✅ Не забанен"
    profiles = await get_user_profiles(session, user.id)
    devices_count = len(profiles)
    safe_username = html.escape(user.username) if user.username else '—'
    safe_first_name = html.escape(user.first_name) if user.first_name else '—'
    referrals = await get_user_referrals(session, user.telegram_id)
    return (f"👤 Карточка пользователя\n─────────────────────────────\n"
            f"<b>ID:</b> {user.telegram_id}\n<b>Username:</b> @{safe_username}\n<b>Имя:</b> {safe_first_name}\n"
            f"<b>Статус:</b> {status}\n<b>Бан:</b> {ban}\n<b>Действует до:</b> {format_datetime(user.subscription_end)}\n"
            f"<b>Осталось:</b> {format_days_left(user.subscription_end)}\n"
            f"<b>Устройств:</b> {devices_count}/{user.device_limit}\n"
            f"<b>Рефералов:</b> {len(referrals)}\n<b>Бонусных дней:</b> +{user.referral_days}\n"
            f"<b>Регистрация:</b> {format_datetime(user.created_at)}")

@router.callback_query(F.data.startswith("admin_user_extend:"))
async def show_extend_options(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()  # 🔥 P1 FIX
    telegram_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(f"⏰ Выберите срок продления для {telegram_id}:",
                                      reply_markup=get_admin_extend_days_keyboard(telegram_id))
    await callback.answer()

@router.callback_query(F.data.startswith("admin_extend_days:"))
async def extend_subscription(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    session = await get_session()
    try:
        await SubscriptionService.extend_subscription(session, telegram_id, days)
        user = await get_user_by_telegram_id(session, telegram_id)
        days_text = "∞ навсегда" if days >= 36500 else f"{days} дней"
        await callback.answer(f"✅ Подписка продлена на {days_text}", show_alert=True)
        logger.info(f"Admin {callback.from_user.id} extended user {telegram_id} by {days} days")
        await _show_user_card_edit(callback.message, user, session)
    finally:
        await session.close()

@router.callback_query(F.data.startswith("admin_extend_custom:"))
async def start_custom_extend(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(f"⌨️ Введите количество дней для продления {telegram_id}:",
                                      reply_markup=get_back_button(f"admin_user_extend:{telegram_id}"))
    await state.set_state(AdminStates.entering_custom_days)
    await state.update_data(target_user_id=telegram_id)
    await callback.answer()

@router.message(AdminStates.entering_custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    # 🔥 P1 FIX
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод. Отправьте число от 1 до 36500:")
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    try:
        days = int(message.text.strip())
        if days < 1 or days > 36500:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 1 до 36500:")
        return
    data = await state.get_data()
    telegram_id = data.get("target_user_id")
    session = await get_session()
    try:
        await SubscriptionService.extend_subscription(session, telegram_id, days)
        user = await get_user_by_telegram_id(session, telegram_id)
        await message.answer(f"✅ Подписка пользователя {telegram_id} продлена на {days} дней.\n"
                              f"Действует до: {format_datetime(user.subscription_end)}",
                              reply_markup=get_admin_user_card_keyboard(telegram_id), parse_mode="HTML")
        logger.info(f"Admin {message.from_user.id} extended user {telegram_id} by {days} days (custom)")
        await state.clear()
    finally:
        await session.close()

@router.callback_query(F.data.startswith("admin_user_ban:"))
async def toggle_ban_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    settings = get_settings()
    if telegram_id in settings.ADMIN_IDS:
        await callback.answer("⛔️ Нельзя банить администраторов", show_alert=True)
        return

    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        new_status = not user.is_banned
        await update_user(session, user, is_banned=new_status)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        has_access = user.subscription_end and user.subscription_end > now
        target_api_status = "disabled" if new_status else ("active" if has_access else "disabled")
        target_db_status = False if new_status else (True if has_access else False)
        profiles = await get_user_profiles(session, user.id)
        server_ids = {p.server_id for p in profiles}
        servers_map = {}
        if server_ids:
            stmt = select(Server).where(Server.id.in_(server_ids))
            res = await session.execute(stmt)
            servers_map = {s.id: s for s in res.scalars().all()}
        tasks_info = []
        profile_ids_to_update = []
        for profile in profiles:
            server = servers_map.get(profile.server_id)
            if server and server.is_active:
                tasks_info.append({'api_url': server.api_url, 'api_key': server.api_key, 'peer_id': profile.peer_id})
                profile_ids_to_update.append(profile.id)
    finally:
        await session.close()

    network_success = True
    if tasks_info:
        async def _update_peer(info, status):
            client = AmneziaClient(info['api_url'], info['api_key'])
            return await client.update_client(client_id=info['peer_id'], status=status)
        tasks = [_update_peer(info, target_api_status) for info in tasks_info]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 🔥 P1 FIX: Проверяем результаты сети
        api_errors = [r for r in results if isinstance(r, Exception) or r is False]
        if api_errors:
            network_success = False

    if not network_success:
        await callback.answer("⚠️ Amnezia API недоступен. БД не изменена.", show_alert=True)
        return

    if profile_ids_to_update:
        session = await get_session()
        try:
            await session.execute(update(VPNProfile).where(VPNProfile.id.in_(profile_ids_to_update))
                                  .values(is_active=target_db_status))
            await session.commit()
        finally:
            await session.close()

    action = "забанен" if new_status else "разбанен"
    alert_text = f"✅ Пользователь {action}"
    if not new_status and not has_access:
        alert_text += " (подписка истекла, устройства оставлены отключенными)"
    await callback.answer(alert_text, show_alert=True)
    logger.info(f"Admin {callback.from_user.id} {action} user {telegram_id}")

    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        await _show_user_card_edit(callback.message, user, session)
    finally:
        await session.close()

@router.callback_query(F.data.startswith("admin_user_devices:"))
async def show_user_devices(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()  # 🔥 P1 FIX
    telegram_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
        profiles = await get_user_profiles(session, user.id)
        text = f"🔧 Устройства пользователя {telegram_id}\n─────────────────────────────\n"
        if not profiles:
            text += "_Устройств нет_\n"
        else:
            for p in profiles:
                safe_device_name = html.escape(p.device_name)
                text += f"📱 <b>{safe_device_name}</b>\n"
                text += f"    Peer: <code>{p.peer_id[:16]}...</code>\n"
                text += f"    Трафик: ↓{p.traffic_down} ↑{p.traffic_up}\n"
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="← К карточке", callback_data=f"admin_user_card:{telegram_id}")
        builder.adjust(1)
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()