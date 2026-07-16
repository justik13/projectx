import logging
import math
from datetime import datetime, timedelta, timezone
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from bot.states import AdminStates
from bot import texts
from utils.tariff_names import get_tariff_display_name, get_tariff_group_name
from utils.formatters import format_datetime, format_days_left, format_user_card_text
from utils.telegram import safe, render_hub
from bot.keyboards.admin.users import (
    get_admin_user_card_keyboard,
    get_admin_subscription_keyboard,
    get_admin_change_tariff_keyboard,
    get_admin_grant_tariff_keyboard,
    get_admin_grant_days_keyboard,
    get_admin_extend_days_new_keyboard,
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
    get_back_button,
)
from database.models import User, Tariff, VPNProfile
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    get_user_count,
    get_user_referrals,
    get_users_paginated_with_profiles,
)
from database.repositories.profiles_repo import get_user_profiles, get_profile_by_id
from database.repositories.servers_repo import get_server_by_id
from database.repositories.tariffs_repo import get_tariff_by_id
from services.subscription import SubscriptionService
from services.device_service import DeviceService
from services.audit_service import AuditService
from config.settings import get_settings
from utils.admin import is_admin

router = Router()
logger = logging.getLogger(__name__)

USERS_PER_PAGE = 10
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2099, 12, 31, 23, 59, 59)

# 🔥 Защита от double-click
_applying_tariffs: set[int] = set()
_applying_extends: set[int] = set()
_applying_reduces: set[int] = set()
_applying_grants: set[int] = set()
_deleting_admin_devices: set[int] = set()


# ──────────────────────────────────────────────────────────
# 🔧 ВСПОМОГАТЕЛЬНЫЕ
# ──────────────────────────────────────────────────────────

def _is_subscription_active(user: User) -> bool:
    if not user.subscription_end:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return user.subscription_end > now


def _format_time_left(subscription_end: datetime) -> str:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = subscription_end - now
    if delta.total_seconds() <= 0:
        return "истекла"
    days = delta.days
    hours = delta.seconds // 3600
    if days >= 36500:
        return "∞ навсегда"
    if days > 0:
        return f"{days} дн. {hours} ч."
    minutes = (delta.seconds % 3600) // 60
    return f"{hours} ч. {minutes} мин."


async def _get_active_tariffs(session: AsyncSession) -> list[Tariff]:
    result = await session.execute(
        select(Tariff).where(Tariff.is_active == True).order_by(Tariff.device_limit)
    )
    return list(result.scalars().all())


async def _get_tariff_groups(session: AsyncSession) -> dict[int, list[Tariff]]:
    """
    Группирует тарифы по device_limit (3 группы: 2, 5, 10+ устр.)
    Возвращает словарь {device_limit: [tariff1, tariff2, ...]}
    """
    tariffs = await _get_active_tariffs(session)
    groups: dict[int, list[Tariff]] = {}
    for t in tariffs:
        limit = t.device_limit
        if limit not in groups:
            groups[limit] = []
        groups[limit].append(t)
    return groups


def _get_representative_tariff(tariffs: list[Tariff]) -> Tariff:
    """
    Выбирает репрезентативный тариф из группы (минимальный срок).
    Используется при выборе группы для выдачи/смены тарифа.
    """
    return min(tariffs, key=lambda t: t.duration_days)


async def _get_user_profiles_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count(VPNProfile.id)).where(VPNProfile.user_id == user_id)
    )
    return result.scalar_one()


async def _get_user_with_profiles(session: AsyncSession, telegram_id: int):
    stmt = (
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.profiles))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────────────────
# 📋 СПИСОК ПОЛЬЗОВАТЕЛЕЙ
# ──────────────────────────────────────────────────────────

async def _build_users_list_text_and_kb(
    users, page: int, total_pages: int, total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = texts.ADMIN_USERS_HEADER.format(
        page=page, total_pages=total_pages, total=total,
    )
    builder = InlineKeyboardBuilder()
    if not users:
        rendered += texts.ADMIN_USERS_EMPTY
    else:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for user in users:
            status = "🟢" if user.subscription_end and user.subscription_end > now else "🔴"
            ban = "🚫" if user.is_banned else ""
            username = f"@{safe(user.username)}" if user.username else f"ID:{user.telegram_id}"
            days = format_days_left(user.subscription_end)
            profiles_count = len(user.profiles) if user.profiles else 0
            builder.button(
                text=f"{status}{ban} {username} · {days} · {profiles_count} устр.",
                callback_data=f"admin_user_card:{user.telegram_id}",
            )
    if page > 1:
        builder.button(text="⬅️", callback_data=f"admin_users_page:{page - 1}")
    if page < total_pages:
        builder.button(text="➡️", callback_data=f"admin_users_page:{page + 1}")
    builder.button(text="🔍 Поиск по ID", callback_data="admin_users_search")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


@router.callback_query(F.data == "admin_users")
async def show_users_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    total_users = await get_user_count(session)
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    users = await get_users_paginated_with_profiles(session, page=1, per_page=USERS_PER_PAGE)
    rendered, kb = await _build_users_list_text_and_kb(users, 1, total_pages, total_users)
    try:
        await callback.message.edit_text(rendered, reply_markup=kb.as_markup(), parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_users_page:"))
async def users_pagination(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    total_users = await get_user_count(session)
    total_pages = max(1, math.ceil(total_users / USERS_PER_PAGE))
    users = await get_users_paginated_with_profiles(session, page=page, per_page=USERS_PER_PAGE)
    rendered, kb = await _build_users_list_text_and_kb(users, page, total_pages, total_users)
    try:
        await callback.message.edit_text(rendered, reply_markup=kb.as_markup(), parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "admin_users_search")
async def start_search_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    try:
        await callback.message.edit_text(
            texts.ADMIN_USER_SEARCH_PROMPT, reply_markup=get_back_button("admin_users"),
        )
    except Exception:
        pass
    await state.set_state(AdminStates.searching_user)


@router.message(AdminStates.searching_user)
async def process_search_user(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await render_hub(message.bot, message.chat.id, texts.ERROR_NUMERIC_ID, get_back_button("admin_users"))
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await render_hub(message.bot, message.chat.id, texts.ERROR_NUMERIC_ID, get_back_button("admin_users"))
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await render_hub(
            message.bot, message.chat.id,
            f"❌ Пользователь с ID {telegram_id} не найден.",
            get_back_button("admin_users"),
        )
        await state.clear()
        return
    await _show_user_card_edit(message, user, session)
    await state.clear()


# ──────────────────────────────────────────────────────────
# 👤 КАРТОЧКА ПОЛЬЗОВАТЕЛЯ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_user_card:"))
async def show_user_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    telegram_id = int(callback.data.split(":")[1])
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_user_card(callback, user, session)


async def _render_user_card(callback: CallbackQuery, user: User, session: AsyncSession):
    profiles = user.profiles if user.profiles else []
    referrals = await get_user_referrals(session, user.telegram_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rendered = format_user_card_text(user, profiles, referrals, now)
    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_user_card_keyboard(user.telegram_id, user.is_banned),
            parse_mode="HTML",
        )
    except Exception:
        pass


async def _show_user_card_edit(message, user, session: AsyncSession):
    profiles = await get_user_profiles(session, user.id)
    referrals = await get_user_referrals(session, user.telegram_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rendered = format_user_card_text(user, profiles, referrals, now)
    try:
        await message.edit_text(
            rendered,
            reply_markup=get_admin_user_card_keyboard(user.telegram_id, user.is_banned),
            parse_mode="HTML",
        )
    except Exception:
        await render_hub(
            message.bot, message.chat.id, rendered,
            get_admin_user_card_keyboard(user.telegram_id, user.is_banned),
        )


# ──────────────────────────────────────────────────────────
# 📅 ПОДМЕНЮ ПОДПИСКИ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_subscription:"))
async def admin_subscription_menu(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    has_active = _is_subscription_active(user)
    profiles_count = await _get_user_profiles_count(session, user.id)
    tariff_name = "—"
    device_limit = user.device_limit or 0
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            device_limit = tariff.device_limit
            tariff_name = f"{get_tariff_display_name(device_limit)} ({device_limit} устр.)"
    if has_active:
        status_block = texts.ADMIN_SUB_STATUS_ACTIVE.format(
            tariff_name=tariff_name,
            valid_until=format_datetime(user.subscription_end),
            time_left=_format_time_left(user.subscription_end),
            devices_count=profiles_count,
            device_limit=device_limit,
        )
    elif user.subscription_end:
        status_block = texts.ADMIN_SUB_STATUS_INACTIVE.format(
            tariff_name=tariff_name,
            valid_until=format_datetime(user.subscription_end),
        )
    else:
        status_block = texts.ADMIN_SUB_STATUS_NONE.format(
            devices_count=profiles_count,
        )
    text = texts.ADMIN_SUBSCRIPTION_HEADER.format(
        telegram_id=telegram_id,
        status_block=status_block,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_subscription_keyboard(telegram_id, has_active),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# 💎 СМЕНА ТАРИФА (показывает 3 группы вместо всех тарифов)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_sub_change_tariff:"))
async def admin_sub_change_tariff(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    groups = await _get_tariff_groups(session)
    profiles_count = len(user.profiles) if user.profiles else 0
    current_tariff_name = "—"
    if user.current_tariff_id:
        t = await get_tariff_by_id(session, user.current_tariff_id)
        if t:
            current_tariff_name = get_tariff_group_name(t.device_limit)
    text = texts.ADMIN_SUB_CHANGE_TARIFF_HEADER.format(
        telegram_id=telegram_id,
        current_tariff=current_tariff_name,
        devices_count=profiles_count,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_change_tariff_keyboard(telegram_id, groups, user.current_tariff_id),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_select_group:"))
async def admin_sub_select_group(callback: CallbackQuery, session: AsyncSession):
    """Выбор группы тарифов при смене тарифа"""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    device_limit = int(parts[2])
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    groups = await _get_tariff_groups(session)
    if device_limit not in groups:
        await callback.answer("❌ Группа тарифов не найдена", show_alert=True)
        return
    tariffs = groups[device_limit]
    new_tariff = _get_representative_tariff(tariffs)
    profiles_count = len(user.profiles) if user.profiles else 0
    new_limit = new_tariff.device_limit
    if profiles_count > new_limit:
        text = texts.ADMIN_SUB_DOWNGRADE_BLOCKED.format(
            telegram_id=telegram_id,
            devices_count=profiles_count,
            new_limit=new_limit,
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_sub_change_tariff:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
        return
    if user.current_tariff_id == new_tariff.id:
        await callback.answer("⚠️ Этот тариф уже выбран", show_alert=True)
        return
    old_tariff_name = "—"
    if user.current_tariff_id:
        old_t = await get_tariff_by_id(session, user.current_tariff_id)
        if old_t:
            old_tariff_name = get_tariff_group_name(old_t.device_limit)
    new_tariff_name = get_tariff_group_name(new_limit)
    text = texts.ADMIN_SUB_CONFIRM_TARIFF.format(
        telegram_id=telegram_id,
        old_tariff=old_tariff_name,
        new_tariff=new_tariff_name,
        devices_count=profiles_count,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_sub_apply_tariff:{telegram_id}:{new_tariff.id}",
                cancel_callback=f"admin_sub_change_tariff:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_apply_tariff:"))
async def admin_sub_apply_tariff(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    lock_key = telegram_id
    if lock_key in _applying_tariffs:
        await callback.answer("⏳ Уже выполняется...", show_alert=True)
        return
    _applying_tariffs.add(lock_key)
    try:
        user = await _get_user_with_profiles(session, telegram_id)
        if not user:
            await callback.message.edit_text("❌ Пользователь не найден.")
            return
        new_tariff = await get_tariff_by_id(session, tariff_id)
        if not new_tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        profiles_count = len(user.profiles) if user.profiles else 0
        if profiles_count > new_tariff.device_limit:
            text = texts.ADMIN_SUB_DOWNGRADE_BLOCKED.format(
                telegram_id=telegram_id,
                devices_count=profiles_count,
                new_limit=new_tariff.device_limit,
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_sub_change_tariff:{telegram_id}"),
                parse_mode="HTML",
            )
            return
        user.current_tariff_id = tariff_id
        user.device_limit = new_tariff.device_limit
        await session.flush()
        await session.commit()
        tariff_name = get_tariff_group_name(new_tariff.device_limit)
        await AuditService.log_action(
            session, callback.from_user.id, "CHANGE_TARIFF", "User", telegram_id,
            f"tariff -> {tariff_name}",
        )
        text = texts.ADMIN_SUB_TARIFF_CHANGED.format(
            telegram_id=telegram_id,
            tariff_name=tariff_name,
            device_limit=new_tariff.device_limit,
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_user_card:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"admin_sub_apply_tariff error: {e}", exc_info=True)
        await session.rollback()
        await callback.answer("❌ Ошибка при смене тарифа", show_alert=True)
    finally:
        _applying_tariffs.discard(lock_key)


# ──────────────────────────────────────────────────────────
# ➕ ПРОДЛЕНИЕ ПОДПИСКИ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_sub_extend:"))
async def admin_sub_extend(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user or not user.subscription_end:
        await callback.answer("❌ У пользователя нет подписки", show_alert=True)
        return
    valid_until = format_datetime(user.subscription_end)
    text = texts.ADMIN_SUB_EXTEND_HEADER.format(
        telegram_id=telegram_id,
        valid_until=valid_until,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_extend_days_new_keyboard(telegram_id),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_confirm_extend:"))
async def admin_sub_confirm_extend(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_end = user.subscription_end if (user.subscription_end and user.subscription_end > now) else now
    new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else current_end + timedelta(days=days)
    days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
    text = texts.ADMIN_SUB_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days_text=days_text,
        new_end=format_datetime(new_end),
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_sub_apply_extend:{telegram_id}:{days}",
                cancel_callback=f"admin_sub_extend:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_apply_extend:"))
async def admin_sub_apply_extend(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    lock_key = telegram_id
    if lock_key in _applying_extends:
        await callback.answer("⏳ Уже выполняется...", show_alert=True)
        return
    _applying_extends.add(lock_key)
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.message.edit_text("❌ Пользователь не найден.")
            return
        await SubscriptionService.extend_subscription(
            session, telegram_id, days,
            new_device_limit=None, new_tariff_id=None,
        )
        user = await get_user_by_telegram_id(session, telegram_id)
        days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
        await AuditService.log_action(
            session, callback.from_user.id, "EXTEND", "User", telegram_id, f"+{days_text}",
        )
        new_end_str = format_datetime(user.subscription_end) if user.subscription_end else "—"
        text = (
            f"✅ <b>Подписка продлена</b>\n"
            f"Пользователь: <code>{telegram_id}</code>\n"
            f"На: <b>{days_text}</b>\n"
            f"Действует до: <b>{new_end_str}</b>"
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_user_card:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"admin_sub_apply_extend error: {e}", exc_info=True)
        await session.rollback()
        await callback.answer("❌ Ошибка при продлении", show_alert=True)
    finally:
        _applying_extends.discard(lock_key)


@router.callback_query(F.data.startswith("admin_sub_extend_custom:"))
async def admin_sub_extend_custom_start(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    await state.clear()
    await state.set_state(AdminStates.admin_extending_custom)
    await state.update_data(admin_telegram_id=telegram_id)
    text = texts.ADMIN_SUB_EXTEND_PROMPT.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(f"admin_sub_extend:{telegram_id}"),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(AdminStates.admin_extending_custom)
async def admin_sub_extend_custom_process(
    message: Message, state: FSMContext, session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    telegram_id = data.get("admin_telegram_id")
    if not telegram_id:
        await state.clear()
        return
    text_input = message.text.strip() if message.text else ""
    if not text_input.isdigit() or int(text_input) < 1:
        await message.answer("⚠️ Введите число ≥ 1")
        return
    days = int(text_input)
    await state.clear()
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_end = user.subscription_end if (user.subscription_end and user.subscription_end > now) else now
    new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else current_end + timedelta(days=days)
    days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
    confirm_text = texts.ADMIN_SUB_CONFIRM_EXTEND.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days_text=days_text,
        new_end=format_datetime(new_end),
    )
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        confirm_text,
        reply_markup=get_admin_confirm_action_keyboard(
            confirm_callback=f"admin_sub_apply_extend:{telegram_id}:{days}",
            cancel_callback=f"admin_sub_extend:{telegram_id}",
        ),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────────────────
# ➖ УМЕНЬШЕНИЕ ДНЕЙ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_sub_reduce:"))
async def admin_sub_reduce_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user or not user.subscription_end:
        await callback.answer("❌ У пользователя нет подписки", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.admin_reducing_days)
    await state.update_data(admin_telegram_id=telegram_id)
    text = texts.ADMIN_SUB_REDUCE_PROMPT.format(
        telegram_id=telegram_id,
        valid_until=format_datetime(user.subscription_end),
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(f"admin_subscription:{telegram_id}"),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(AdminStates.admin_reducing_days)
async def admin_sub_reduce_process(
    message: Message, state: FSMContext, session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    telegram_id = data.get("admin_telegram_id")
    if not telegram_id:
        await state.clear()
        return
    text_input = message.text.strip() if message.text else ""
    if not text_input.isdigit() or int(text_input) < 1:
        await message.answer("⚠️ Введите число ≥ 1")
        return
    days = int(text_input)
    await state.clear()
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user or not user.subscription_end:
        await message.answer("❌ У пользователя нет активной подписки.")
        return
    current_end = user.subscription_end
    new_end = current_end - timedelta(days=days)
    confirm_text = texts.ADMIN_SUB_CONFIRM_REDUCE.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days=days,
        new_end=format_datetime(new_end),
    )
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        confirm_text,
        reply_markup=get_admin_confirm_action_keyboard(
            confirm_callback=f"admin_sub_apply_reduce:{telegram_id}:{days}",
            cancel_callback=f"admin_subscription:{telegram_id}",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_sub_apply_reduce:"))
async def admin_sub_apply_reduce(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])
    lock_key = f"reduce_{telegram_id}"
    if lock_key in _applying_reduces:
        await callback.answer("⏳ Уже выполняется...", show_alert=True)
        return
    _applying_reduces.add(lock_key)
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user or not user.subscription_end:
            await callback.message.edit_text("❌ У пользователя нет подписки.")
            return
        new_end = user.subscription_end - timedelta(days=days)
        user.subscription_end = new_end
        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False
        await session.flush()
        await session.commit()
        await AuditService.log_action(
            session, callback.from_user.id, "REDUCE", "User", telegram_id,
            f"-{days} days -> {format_datetime(new_end)}",
        )
        text = texts.ADMIN_SUB_REDUCED.format(
            telegram_id=telegram_id,
            new_end=format_datetime(new_end),
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_user_card:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"admin_sub_apply_reduce error: {e}", exc_info=True)
        await session.rollback()
        await callback.answer("❌ Ошибка при уменьшении", show_alert=True)
    finally:
        _applying_reduces.discard(lock_key)


# ──────────────────────────────────────────────────────────
# 🎫 ВЫДАЧА ДОСТУПА (показывает 3 группы вместо всех тарифов)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_sub_grant:"))
async def admin_sub_grant(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    groups = await _get_tariff_groups(session)
    text = texts.ADMIN_SUB_GRANT_HEADER.format(telegram_id=telegram_id)
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_grant_tariff_keyboard(telegram_id, groups),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_grant_group:"))
async def admin_sub_grant_group(callback: CallbackQuery, session: AsyncSession):
    """Выбор группы тарифов при выдаче доступа"""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    device_limit = int(parts[2])
    groups = await _get_tariff_groups(session)
    if device_limit not in groups:
        await callback.answer("❌ Группа тарифов не найдена", show_alert=True)
        return
    tariffs = groups[device_limit]
    tariff = _get_representative_tariff(tariffs)
    tariff_name = get_tariff_group_name(tariff.device_limit)
    text = texts.ADMIN_SUB_GRANT_DAYS_HEADER.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_grant_days_keyboard(telegram_id, tariff.id),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_grant_confirm:"))
async def admin_sub_grant_confirm(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    days = int(parts[3])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else now + timedelta(days=days)
    days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
    tariff_name = get_tariff_group_name(tariff.device_limit)
    text = texts.ADMIN_SUB_CONFIRM_GRANT.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
        days_text=days_text,
        new_end=format_datetime(new_end),
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_sub_grant_apply:{telegram_id}:{tariff_id}:{days}",
                cancel_callback=f"admin_sub_grant_group:{telegram_id}:{tariff.device_limit}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_grant_custom:"))
async def admin_sub_grant_custom_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminStates.admin_grant_custom_days)
    await state.update_data(admin_telegram_id=telegram_id, admin_tariff_id=tariff_id)
    tariff_name = get_tariff_group_name(tariff.device_limit)
    text = texts.ADMIN_SUB_GRANT_CUSTOM_PROMPT.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(f"admin_sub_grant_group:{telegram_id}:{tariff.device_limit}"),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(AdminStates.admin_grant_custom_days)
async def admin_sub_grant_custom_process(
    message: Message, state: FSMContext, session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    telegram_id = data.get("admin_telegram_id")
    tariff_id = data.get("admin_tariff_id")
    if not telegram_id or not tariff_id:
        await state.clear()
        return
    text_input = message.text.strip() if message.text else ""
    if not text_input.isdigit() or int(text_input) < 1:
        await message.answer("⚠️ Введите число ≥ 1")
        return
    days = int(text_input)
    await state.clear()
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await message.answer("❌ Тариф не найден.")
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else now + timedelta(days=days)
    days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
    tariff_name = get_tariff_group_name(tariff.device_limit)
    confirm_text = texts.ADMIN_SUB_CONFIRM_GRANT.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
        days_text=days_text,
        new_end=format_datetime(new_end),
    )
    try:
        await message.delete()
    except Exception:
        pass
    await message.answer(
        confirm_text,
        reply_markup=get_admin_confirm_action_keyboard(
            confirm_callback=f"admin_sub_grant_apply:{telegram_id}:{tariff_id}:{days}",
            cancel_callback=f"admin_sub_grant_group:{telegram_id}:{tariff.device_limit}",
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin_sub_grant_apply:"))
async def admin_sub_grant_apply(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    days = int(parts[3])
    lock_key = f"grant_{telegram_id}"
    if lock_key in _applying_grants:
        await callback.answer("⏳ Уже выполняется...", show_alert=True)
        return
    _applying_grants.add(lock_key)
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        await SubscriptionService.extend_subscription(
            session, telegram_id, days,
            new_device_limit=tariff.device_limit,
            new_tariff_id=tariff.id,
        )
        days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
        tariff_name = get_tariff_group_name(tariff.device_limit)
        await AuditService.log_action(
            session, callback.from_user.id, "GRANT", "User", telegram_id,
            f"{tariff_name} / {days_text}",
        )
        user = await get_user_by_telegram_id(session, telegram_id)
        new_end_str = format_datetime(user.subscription_end) if user and user.subscription_end else "—"
        text = (
            f"✅ <b>Доступ выдан</b>\n"
            f"Пользователь: <code>{telegram_id}</code>\n"
            f"Тариф: <b>{tariff_name}</b>\n"
            f"Срок: <b>{days_text}</b>\n"
            f"Действует до: <b>{new_end_str}</b>"
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_user_card:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"admin_sub_grant_apply error: {e}", exc_info=True)
        await session.rollback()
        await callback.answer("❌ Ошибка при выдаче доступа", show_alert=True)
    finally:
        _applying_grants.discard(lock_key)


# ──────────────────────────────────────────────────────────
# 🔧 УПРАВЛЕНИЕ УСТРОЙСТВАМИ + УДАЛЕНИЕ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_user_devices:"))
async def admin_user_devices(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    profiles = user.profiles if user.profiles else []
    if not profiles:
        text = texts.ADMIN_USER_DEVICES_HEADER.format(telegram_id=telegram_id) + "\n" + texts.ADMIN_USER_DEVICES_EMPTY
    else:
        text = texts.ADMIN_USER_DEVICES_HEADER.format(telegram_id=telegram_id)
        for p in profiles:
            name = getattr(p, "device_name", None) or f"Устройство #{p.id}"
            text += f"\n• {safe(name)}"
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_devices_keyboard(telegram_id, profiles),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_delete_device:"))
async def admin_delete_device_confirm(callback: CallbackQuery, session: AsyncSession):
    """Показывает подтверждение удаления устройства"""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    profile_id = int(parts[2])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
        return
    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else "🌍"
    server_name = server.name if server else "Неизвестно"
    text = texts.ADMIN_DELETE_DEVICE_CONFIRM.format(
        telegram_id=telegram_id,
        device_name=safe(profile.device_name),
        flag=flag,
        server_name=safe(server_name),
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_delete_device_apply:{telegram_id}:{profile_id}",
                cancel_callback=f"admin_user_devices:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_delete_device_apply:"))
async def admin_delete_device_apply(callback: CallbackQuery, session: AsyncSession):
    """Применяет удаление устройства"""
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    profile_id = int(parts[2])
    lock_key = profile_id
    if lock_key in _deleting_admin_devices:
        await callback.answer("⏳ Уже удаляется...", show_alert=True)
        return
    _deleting_admin_devices.add(lock_key)
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
            return
        device_name = profile.device_name
        success = await DeviceService.delete_device(session, profile)
        if not success:
            await callback.answer(
                "⚠️ Не удалось удалить устройство. API сервера недоступен.",
                show_alert=True,
            )
            return
        await AuditService.log_action(
            session, callback.from_user.id, "DELETE_DEVICE", "VPNProfile", profile_id,
            f"user={telegram_id}, device={device_name}",
        )
        text = texts.ADMIN_DELETE_DEVICE_SUCCESS.format(
            telegram_id=telegram_id,
            device_name=safe(device_name),
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(f"admin_user_devices:{telegram_id}"),
                parse_mode="HTML",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"admin_delete_device_apply error: {e}", exc_info=True)
        await session.rollback()
        await callback.answer("❌ Ошибка при удалении устройства", show_alert=True)
    finally:
        _deleting_admin_devices.discard(lock_key)


# ──────────────────────────────────────────────────────────
# 🚫 БАН / РАЗБАН (с подтверждением)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban_confirm(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    settings = get_settings()
    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(texts.ERROR_ADMIN_BAN_FORBIDDEN, show_alert=True)
        return
    text = (
        f"⚠️ <b>Подтверждение бана</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n"
        f"Пользователь будет заблокирован и не сможет использовать бота.\n"
        f"<i>Это действие можно отменить.</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_ban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_ban_apply:"))
async def admin_ban_apply(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    settings = get_settings()
    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(texts.ERROR_ADMIN_BAN_FORBIDDEN, show_alert=True)
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    user.is_banned = True
    await session.flush()
    await session.commit()
    await AuditService.log_action(
        session, callback.from_user.id, "BAN", "User", telegram_id, "",
    )
    user = await _get_user_with_profiles(session, telegram_id)
    if user:
        await _render_user_card(callback, user, session)


@router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban_confirm(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    text = (
        f"⚠️ <b>Подтверждение разбана</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n"
        f"Пользователь будет разблокирован.\n"
        f"<i>Это действие можно отменить.</i>"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_unban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_unban_apply:"))
async def admin_unban_apply(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    telegram_id = int(callback.data.split(":")[1])
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return
    user.is_banned = False
    await session.flush()
    await session.commit()
    await AuditService.log_action(
        session, callback.from_user.id, "UNBAN", "User", telegram_id, "",
    )
    user = await _get_user_with_profiles(session, telegram_id)
    if user:
        await _render_user_card(callback, user, session)