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
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.connection import get_session
from database.repositories.users_repo import (
    get_user_by_telegram_id, get_users_paginated, get_user_count, update_user, get_user_referrals
)
from database.repositories.profiles_repo import get_user_profiles
from services.subscription import SubscriptionService
from services.audit_service import AuditService
from bot.keyboards import (
    get_admin_user_card_keyboard,
    get_admin_extend_days_keyboard, get_back_button
)
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
        text, kb = await _build_users_list_text_and_kb(users, 1, total_pages, total_users)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
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
        text, kb = await _build_users_list_text_and_kb(users, page, total_pages, total_users)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()

async def _build_users_list_text_and_kb(users, page: int, total_pages: int, total: int) -> tuple[str, InlineKeyboardBuilder]:
    text = (
        f"🛠 Админка › 👥 <b>Пользователи</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n"
    )
    builder = InlineKeyboardBuilder()
    if not users:
        text += "_Пользователей пока нет_\n"
    else:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for user in users:
            status = "🟢" if user.subscription_end and user.subscription_end > now else "🔴"
            ban = "🚫" if user.is_banned else ""
            username = f"@{html.escape(user.username)}" if user.username else f"ID:{user.telegram_id}"
            days = format_days_left(user.subscription_end)
            btn_text = f"{status}{ban} {username} · {days}"
            builder.button(text=btn_text, callback_data=f"admin_user_card:{user.telegram_id}")

    # Кнопки пагинации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(("⬅️", f"admin_users_page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(("➡️", f"admin_users_page:{page + 1}"))
    for btn_text, btn_data in nav_buttons:
        builder.button(text=btn_text, callback_data=btn_data)

    builder.button(text="🔍 Поиск по ID", callback_data="admin_users_search")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return text, builder

@router.callback_query(F.data == "admin_users_search")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>\n"
        "Введите Telegram ID пользователя:",
        reply_markup=get_back_button("admin_users"),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.searching_user)
    await callback.answer()

@router.message(AdminStates.searching_user)
async def process_search_user(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
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
async def show_user_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
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
    await message.answer(
        text,
        reply_markup=get_admin_user_card_keyboard(user.telegram_id),
        parse_mode="HTML"
    )

async def _show_user_card_edit(message, user, session):
    text = await _build_user_card_text(user, session)
    try:
        await message.edit_text(
            text,
            reply_markup=get_admin_user_card_keyboard(user.telegram_id),
            parse_mode="HTML"
        )
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

    return (
        f"🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>\n"
        f"<b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"<b>Username:</b> @{safe_username}\n"
        f"<b>Имя:</b> {safe_first_name}\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Бан:</b> {ban}\n"
        f"<b>Действует до:</b> {format_datetime(user.subscription_end)}\n"
        f"<b>Осталось:</b> {format_days_left(user.subscription_end)}\n"
        f"<b>Устройств:</b> {devices_count}/{user.device_limit}\n"
        f"<b>Рефералов:</b> {len(referrals)}\n"
        f"<b>Бонусных дней:</b> +{user.referral_days}\n"
        f"<b>Регистрация:</b> {format_datetime(user.created_at)}"
    )

@router.callback_query(F.data.startswith("admin_user_extend:"))
async def show_extend_options(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        f"🛠 Админка › 👥 Пользователи › ⏰ <b>Продление доступа</b>\n"
        f"Выберите срок продления для <code>{telegram_id}</code>:",
        reply_markup=get_admin_extend_days_keyboard(telegram_id),
        parse_mode="HTML"
    )
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
        await AuditService.log_action(
            session, callback.from_user.id, "EXTEND", "User", telegram_id, f"{days} days"
        )
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
    await callback.message.edit_text(
        f"🛠 Админка › 👥 Пользователи › ⌨️ <b>Ручное продление</b>\n"
        f"Введите количество дней для продления <code>{telegram_id}</code>:",
        reply_markup=get_back_button(f"admin_user_extend:{telegram_id}"),
        parse_mode="HTML"
    )
    await state.set_state(AdminStates.entering_custom_days)
    await state.update_data(target_user_id=telegram_id)
    await callback.answer()

@router.message(AdminStates.entering_custom_days)
async def process_custom_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
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
        await AuditService.log_action(
            session, message.from_user.id, "EXTEND", "User", telegram_id, f"{days} days (custom)"
        )
        user = await get_user_by_telegram_id(session, telegram_id)
        await message.answer(
            f"✅ Подписка пользователя <code>{telegram_id}</code> продлена на {days} дней.\n"
            f"Действует до: {format_datetime(user.subscription_end)}",
            reply_markup=get_admin_user_card_keyboard(telegram_id),
            parse_mode="HTML"
        )
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
        await AuditService.log_action(
            session, callback.from_user.id,
            "BAN" if new_status else "UNBAN",
            "User", telegram_id
        )

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
                tasks_info.append({
                    'api_url': server.api_url,
                    'api_key': server.api_key,
                    'peer_id': profile.peer_id
                })
                profile_ids_to_update.append(profile.id)
    finally:
        await session.close()

    network_success = True
    if tasks_info:
        # 🔥 ИСПРАВЛЕНИЕ: Семафор для ограничения одновременных запросов к API
        sem = asyncio.Semaphore(20)
        async def _update_peer(info, status):
            async with sem:
                client = AmneziaClient(info['api_url'], info['api_key'])
                return await client.update_client(client_id=info['peer_id'], status=status)

        tasks = [_update_peer(info, target_api_status) for info in tasks_info]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        api_errors = [r for r in results if isinstance(r, Exception) or r is False]
        if api_errors:
            network_success = False

    if not network_success:
        await callback.answer("⚠️ Amnezia API недоступен. БД не изменена.", show_alert=True)
        return

    if profile_ids_to_update:
        session = await get_session()
        try:
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids_to_update))
                .values(is_active=target_db_status)
            )
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
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        profiles = await get_user_profiles(session, user.id)
        text = (
            f"🛠 Админка › 👥 Пользователи › 🔧 <b>Устройства</b>\n"
            f"Пользователь <code>{telegram_id}</code>\n"
        )
        if not profiles:
            text += "_Устройств нет_\n"
        else:
            for p in profiles:
                safe_device_name = html.escape(p.device_name)
                text += (
                    f"📱 <b>{safe_device_name}</b>\n"
                    f"Peer: <code>{p.peer_id[:16]}...</code>\n"
                    f"Трафик: ↓{p.traffic_down} ↑{p.traffic_up}\n"
                )

        builder = InlineKeyboardBuilder()
        builder.button(text="← К карточке пользователя", callback_data=f"admin_user_card:{telegram_id}")
        builder.adjust(1)
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()
