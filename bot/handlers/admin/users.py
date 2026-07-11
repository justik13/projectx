import html
import logging
import math
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import (
    get_user_by_telegram_id, get_users_paginated, get_user_count, get_user_referrals
)
from database.repositories.profiles_repo import get_user_profiles
from services.subscription import SubscriptionService
from services.ban_service import BanService
from services.audit_service import AuditService
from bot.keyboards import (
    get_admin_user_card_keyboard, get_admin_extend_days_keyboard, get_back_button
)
from bot.states import AdminStates
from utils.formatters import format_datetime, format_days_left
from utils.admin import is_admin
from utils.telegram import safe
from config.settings import get_settings

router = Router()
logger = logging.getLogger(__name__)
USERS_PER_PAGE = 10


async def _build_users_list_text_and_kb(users, page: int, total_pages: int, total: int) -> tuple[str, InlineKeyboardBuilder]:
    text = (
        f"🛠 Админка › 👥 <b>Пользователи</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n\n"
    )
    builder = InlineKeyboardBuilder()
    if not users:
        text += "_Пользователей пока нет_\n"
    else:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for user in users:
            status = "🟢" if user.subscription_end and user.subscription_end > now else "🔴"
            ban = "🚫" if user.is_banned else ""
            username = f"@{safe(user.username)}" if user.username else f"ID:{user.telegram_id}"
            days = format_days_left(user.subscription_end)
            btn_text = f"{status}{ban} {username} · {days}"
            builder.button(text=btn_text, callback_data=f"admin_user_card:{user.telegram_id}")
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


@router.callback_query(F.data == "admin_users")
async def show_users_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    total_users = await get_user_count(session)
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    users = await get_users_paginated(session, page=1, per_page=USERS_PER_PAGE)
    text, kb = await _build_users_list_text_and_kb(users, 1, total_pages, total_users)
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    total_users = await get_user_count(session)
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    users = await get_users_paginated(session, page=page, per_page=USERS_PER_PAGE)
    text, kb = await _build_users_list_text_and_kb(users, page, total_pages, total_users)
    await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_users_search")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>\nВведите Telegram ID пользователя:",
        reply_markup=get_back_button("admin_users"), parse_mode="HTML"
    )
    await state.set_state(AdminStates.searching_user)
    await callback.answer()


@router.message(AdminStates.searching_user)
async def process_search_user(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Отправьте числовой Telegram ID:")
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите числовой ID:")
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await message.answer(f"❌ Пользователь с ID {telegram_id} не найден.", reply_markup=get_back_button("admin_users"))
        await state.clear()
        return
    await _show_user_card(message, user, session)
    await state.clear()


@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    await _show_user_card_edit(callback.message, user, session)
    await callback.answer()


async def _show_user_card(message: Message, user, session):
    text = await _build_user_card_text(user, session)
    await message.answer(text, reply_markup=get_admin_user_card_keyboard(user.telegram_id), parse_mode="HTML")


async def _show_user_card_edit(message, user, session):
    text = await _build_user_card_text(user, session)
    try:
        await message.edit_text(text, reply_markup=get_admin_user_card_keyboard(user.telegram_id), parse_mode="HTML")
    except Exception:
        pass


async def _build_user_card_text(user, session) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    has_access = user.subscription_end and user.subscription_end > now
    status = "🟢 Активен" if has_access else "🔴 Неактивен"
    ban = "🚫 ЗАБАНЕН" if user.is_banned else "✅ Не забанен"
    profiles = await get_user_profiles(session, user.id)
    devices_count = len(profiles)
    referrals = await get_user_referrals(session, user.telegram_id)
    return (
        f"🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>\n"
        f"<b>ID:</b> <code>{user.telegram_id}</code>\n"
        f"<b>Username:</b> @{safe(user.username)}\n"
        f"<b>Имя:</b> {safe(user.first_name)}\n"
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
        reply_markup=get_admin_extend_days_keyboard(telegram_id), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_extend_days:"))
async def extend_subscription(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    await SubscriptionService.extend_subscription(session, telegram_id, days)
    await AuditService.log_action(session, callback.from_user.id, "EXTEND", "User", telegram_id, f"{days} days")
    user = await get_user_by_telegram_id(session, telegram_id)
    days_text = "∞ навсегда" if days >= 36500 else f"{days} дней"
    await callback.answer(f"✅ Подписка продлена на {days_text}", show_alert=True)
    await _show_user_card_edit(callback.message, user, session)


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
        reply_markup=get_back_button(f"admin_user_extend:{telegram_id}"), parse_mode="HTML"
    )
    await state.set_state(AdminStates.entering_custom_days)
    await state.update_data(target_user_id=telegram_id)
    await callback.answer()


@router.message(AdminStates.entering_custom_days)
async def process_custom_days(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Отправьте число от 1 до 36500:")
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
    await SubscriptionService.extend_subscription(session, telegram_id, days)
    await AuditService.log_action(session, message.from_user.id, "EXTEND", "User", telegram_id, f"{days} days (custom)")
    user = await get_user_by_telegram_id(session, telegram_id)
    await message.answer(
        f"✅ Подписка пользователя <code>{telegram_id}</code> продлена на {days} дней.\n"
        f"Действует до: {format_datetime(user.subscription_end)}",
        reply_markup=get_admin_user_card_keyboard(telegram_id), parse_mode="HTML"
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin_user_ban:"))
async def toggle_ban_user(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    settings = get_settings()
    if telegram_id in settings.ADMIN_IDS:
        await callback.answer("⛔️ Нельзя банить администраторов", show_alert=True)
        return
    success, result = await BanService.toggle_ban(session, callback.from_user.id, telegram_id)
    if not success:
        await callback.answer(f"⚠️ {result}", show_alert=True)
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    await _show_user_card_edit(callback.message, user, session)
    await callback.answer(f"✅ Пользователь {result}", show_alert=True)


@router.callback_query(F.data.startswith("admin_user_devices:"))
async def show_user_devices(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    profiles = await get_user_profiles(session, user.id)
    text = (
        f"🛠 Админка › 👥 Пользователи › 🔧 <b>Устройства</b>\n"
        f"Пользователь <code>{telegram_id}</code>\n\n"
    )
    if not profiles:
        text += "_Устройств нет_\n"
    else:
        for p in profiles:
            text += (
                f"📱 <b>{safe(p.device_name)}</b>\n"
                f"Peer: <code>{p.peer_id[:16]}...</code>\n"
                f"Трафик: ↓{p.traffic_down} ↑{p.traffic_up}\n\n"
            )
    builder = InlineKeyboardBuilder()
    builder.button(text="← К карточке пользователя", callback_data=f"admin_user_card:{telegram_id}")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()