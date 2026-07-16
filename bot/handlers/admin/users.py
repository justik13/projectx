import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.states import AdminStates
from bot import texts
from utils.tariff_names import get_tariff_display_name, get_tariff_group_name
from utils.formatters import format_datetime
from utils.telegram import safe
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
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.tariffs_repo import get_tariff_by_id
from services.subscription import SubscriptionService
from services.audit_service import AuditService
from utils.admin import is_admin

router = Router()
logger = logging.getLogger(__name__)

PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2099, 12, 31, 23, 59, 59)

# 🔥 Защита от double-click
_applying_tariffs: set[int] = set()
_applying_extends: set[int] = set()
_applying_reduces: set[int] = set()
_applying_grants: set[int] = set()


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


async def _get_user_profiles_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(VPNProfile).where(VPNProfile.user_id == user_id)
    )
    return len(result.scalars().all())


async def _get_user_with_profiles(session: AsyncSession, telegram_id: int):
    stmt = (
        select(User)
        .where(User.telegram_id == telegram_id)
        .options(selectinload(User.profiles))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ──────────────────────────────────────────────────────────
# 👤 КАРТОЧКА ПОЛЬЗОВАТЕЛЯ
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_user_card:"))
async def admin_user_card(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = int(callback.data.split(":")[1])
    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        try:
            await callback.message.edit_text("❌ Пользователь не найден.")
        except Exception:
            pass
        return

    await _render_user_card(callback, user, session)


async def _render_user_card(callback: CallbackQuery, user: User, session: AsyncSession):
    profiles = user.profiles if user.profiles else []
    has_access = _is_subscription_active(user)
    tariff_name = "—"
    device_limit = user.device_limit or 0

    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            device_limit = tariff.device_limit
            tariff_name = f"{get_tariff_display_name(device_limit)} ({device_limit} устр.)"

    if has_access:
        status_emoji = "🟢"
        sub_status = "Активна"
        valid_until = format_datetime(user.subscription_end)
        time_left = _format_time_left(user.subscription_end)
        status_block = (
            f"{status_emoji} <b>{sub_status}</b>\n"
            f"💎 <b>Тариф:</b> {tariff_name}\n"
            f"📅 <b>Действует до:</b> {valid_until}\n"
            f"⏱ <b>Осталось:</b> {time_left}\n"
            f"🔌 <b>Устройств:</b> {len(profiles)} / {device_limit}"
        )
    elif user.subscription_end:
        status_block = (
            f"🔴 <b>Истекла</b>\n"
            f"💎 <b>Тариф:</b> {tariff_name}\n"
            f"📅 <b>Истекла:</b> {format_datetime(user.subscription_end)}"
        )
    else:
        status_block = (
            f"⚪ <b>Нет подписки</b>\n"
            f"💎 <b>Тариф:</b> —\n"
            f"🔌 <b>Устройств:</b> {len(profiles)}"
        )

    ban_emoji = "🚫 ЗАБАНЕН" if user.is_banned else "✅ Активен"
    text = (
        f"👤 <b>Карточка пользователя</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: <code>{user.telegram_id}</code>\n"
        f"👤 Имя: {safe(user.first_name or '—')}\n"
        f"📛 Username: @{safe(user.username or '—')}\n"
        f"📅 Регистрация: {format_datetime(user.created_at)}\n"
        f"🛡 Статус: {ban_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_block}"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_card_keyboard(user.telegram_id),
            parse_mode="HTML",
        )
    except Exception:
        pass


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
# 💎 СМЕНА ТАРИФА (активная подписка)
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

    tariffs = await _get_active_tariffs(session)
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
            reply_markup=get_admin_change_tariff_keyboard(telegram_id, tariffs, user.current_tariff_id),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_select_tariff:"))
async def admin_sub_select_tariff(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return

    new_tariff = await get_tariff_by_id(session, tariff_id)
    if not new_tariff:
        await callback.answer("❌ Тариф не найден", show_alert=True)
        return

    profiles_count = len(user.profiles) if user.profiles else 0
    new_limit = new_tariff.device_limit

    # ── Проверка даунгрейда ──
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

    # ── Если тот же тариф ──
    if user.current_tariff_id == tariff_id:
        await callback.answer("⚠️ Этот тариф уже выбран", show_alert=True)
        return

    # ── Подтверждение ──
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
                confirm_callback=f"admin_sub_apply_tariff:{telegram_id}:{tariff_id}",
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

    # 🔥 Защита от double-click
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

        # ── Применяем ──
        user.current_tariff_id = tariff_id
        user.device_limit = new_tariff.device_limit
        await session.flush()
        await session.commit()

        tariff_name = get_tariff_group_name(new_tariff.device_limit)

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "CHANGE_TARIFF",
            "User",
            telegram_id,
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
            session,
            telegram_id,
            days,
            new_device_limit=None,
            new_tariff_id=None,
        )

        # Перечитываем для актуальной даты
        user = await get_user_by_telegram_id(session, telegram_id)
        days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "EXTEND",
            "User",
            telegram_id,
            f"+{days_text}",
        )

        new_end_str = format_datetime(user.subscription_end) if user.subscription_end else "—"
        text = f"✅ <b>Подписка продлена</b>\nПользователь: <code>{telegram_id}</code>\nНа: <b>{days_text}</b>\nДействует до: <b>{new_end_str}</b>"

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


# ── Кастомное продление (FSM) ──

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
    message: Message,
    state: FSMContext,
    session: AsyncSession,
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

    # Удаляем сообщение пользователя
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
    message: Message,
    state: FSMContext,
    session: AsyncSession,
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
            session,
            callback.from_user.id,
            "REDUCE",
            "User",
            telegram_id,
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
# 🎫 ВЫДАЧА ДОСТУПА (нет подписки)
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_sub_grant:"))
async def admin_sub_grant(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = int(callback.data.split(":")[1])
    tariffs = await _get_active_tariffs(session)

    text = texts.ADMIN_SUB_GRANT_HEADER.format(telegram_id=telegram_id)

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_grant_tariff_keyboard(telegram_id, tariffs),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith("admin_sub_grant_tariff:"))
async def admin_sub_grant_tariff(callback: CallbackQuery, session: AsyncSession):
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

    tariff_name = get_tariff_group_name(tariff.device_limit)
    text = texts.ADMIN_SUB_GRANT_DAYS_HEADER.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_grant_days_keyboard(telegram_id, tariff_id),
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
                cancel_callback=f"admin_sub_grant_tariff:{telegram_id}:{tariff_id}",
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ── Кастомные дни для выдачи (FSM) ──

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
            reply_markup=get_back_button(f"admin_sub_grant_tariff:{telegram_id}:{tariff_id}"),
            parse_mode="HTML",
        )
    except Exception:
        pass


@router.message(AdminStates.admin_grant_custom_days)
async def admin_sub_grant_custom_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
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
            cancel_callback=f"admin_sub_grant_tariff:{telegram_id}:{tariff_id}",
        ),
        parse_mode="HTML",
    )


# ── Применение выдачи ──

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
            session,
            telegram_id,
            days,
            new_device_limit=tariff.device_limit,
            new_tariff_id=tariff.id,
        )

        days_text = "∞ навсегда" if days >= PERMANENT_SUBSCRIPTION_DAYS else f"{days} дн."
        tariff_name = get_tariff_group_name(tariff.device_limit)

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "GRANT",
            "User",
            telegram_id,
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
# 🔧 УПРАВЛЕНИЕ УСТРОЙСТВАМИ
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
        text = texts.ADMIN_USER_DEVICES_HEADER.format(telegram_id=telegram_id) + "\n\n" + texts.ADMIN_USER_DEVICES_EMPTY
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


# ──────────────────────────────────────────────────────────
# 🚫 БАН / РАЗБАН
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = int(callback.data.split(":")[1])
    text = (
        f"⚠️ <b>Подтверждение бана</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n\n"
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
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.message.edit_text("❌ Пользователь не найден.")
        return

    user.is_banned = True
    await session.flush()
    await session.commit()

    await AuditService.log_action(session, callback.from_user.id, "BAN", "User", telegram_id, "")

    user = await _get_user_with_profiles(session, telegram_id)
    if user:
        await _render_user_card(callback, user, session)


@router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    telegram_id = int(callback.data.split(":")[1])
    text = (
        f"⚠️ <b>Подтверждение разбана</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n\n"
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

    await AuditService.log_action(session, callback.from_user.id, "UNBAN", "User", telegram_id, "")

    user = await _get_user_with_profiles(session, telegram_id)
    if user:
        await _render_user_card(callback, user, session)