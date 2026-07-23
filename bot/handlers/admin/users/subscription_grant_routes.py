import logging
from datetime import timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import (
    PERMANENT_END_DATE,
    PERMANENT_SUBSCRIPTION_DAYS,
)
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_grant_days_keyboard,
    get_admin_grant_tariff_keyboard,
)
from bot.middlewares.user_context import invalidate_user_cache
from bot.states import AdminStates
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_group_name
from utils.telegram import render_hub

from .common import (
    _get_representative_tariff,
    _get_tariff_groups,
    _validate_positive_int,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_sub_grant:"))
async def admin_sub_grant(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = int(callback.data.split(":")[1])

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    if user.is_deleted:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_USER_DELETED,
            show_alert=True,
        )
        return

    if user.is_banned:
        await callback.answer(
            texts.ADMIN_MANUAL_GRANT_USER_BANNED,
            show_alert=True,
        )
        return

    groups = await _get_tariff_groups(session)

    text = texts.ADMIN_SUB_GRANT_HEADER.format(
        telegram_id=telegram_id
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_grant_tariff_keyboard(
                telegram_id,
                groups,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_sub_grant edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_sub_grant_group:"))
async def admin_sub_grant_group(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    device_limit = int(parts[2])

    groups = await _get_tariff_groups(session)

    if device_limit not in groups:
        await callback.answer(
            texts.ADMIN_SUB_GROUP_NOT_FOUND,
            show_alert=True,
        )
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
            reply_markup=get_admin_grant_days_keyboard(
                telegram_id,
                tariff.id,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_sub_grant_group edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_sub_grant_confirm:"))
async def admin_sub_grant_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    days = int(parts[3])

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    current_time = now_utc()
    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_time + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else f"{days} дн."
    )

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
                confirm_callback=(
                    f"admin_sub_grant_apply:"
                    f"{telegram_id}:{tariff_id}:{days}"
                ),
                cancel_callback=(
                    f"admin_sub_grant_group:"
                    f"{telegram_id}:{tariff.device_limit}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_sub_grant_confirm edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_sub_grant_custom:"))
async def admin_sub_grant_custom_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(AdminStates.admin_grant_custom_days)
    await state.update_data(
        admin_telegram_id=telegram_id,
        admin_tariff_id=tariff_id,
    )

    tariff_name = get_tariff_group_name(tariff.device_limit)

    text = texts.ADMIN_SUB_GRANT_CUSTOM_PROMPT.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(
                f"admin_sub_grant_group:"
                f"{telegram_id}:{tariff.device_limit}"
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            "admin_sub_grant_custom_start "
            f"edit_text failed: {e}"
        )


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

    days = _validate_positive_int(message.text)
    if days is None:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_DAYS_OVERFLOW,
            get_back_button(
                f"admin_sub_grant_group:{telegram_id}:{tariff_id}"
            ),
            parse_mode="HTML",
        )
        return

    await state.clear()

    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TARIFF_NOT_FOUND,
            get_back_button(f"admin_subscription:{telegram_id}"),
        )
        return

    current_time = now_utc()
    new_end = (
        PERMANENT_END_DATE
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else current_time + timedelta(days=days)
    )

    days_text = (
        texts.ADMIN_SUB_PERMANENT_LABEL
        if days >= PERMANENT_SUBSCRIPTION_DAYS
        else f"{days} дн."
    )

    tariff_name = get_tariff_group_name(tariff.device_limit)

    confirm_text = texts.ADMIN_SUB_CONFIRM_GRANT.format(
        telegram_id=telegram_id,
        tariff_name=tariff_name,
        days_text=days_text,
        new_end=format_datetime(new_end),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        confirm_text,
        get_admin_confirm_action_keyboard(
            confirm_callback=(
                f"admin_sub_grant_apply:"
                f"{telegram_id}:{tariff_id}:{days}"
            ),
            cancel_callback=(
                f"admin_sub_grant_group:"
                f"{telegram_id}:{tariff.device_limit}"
            ),
        ),
    )


@router.callback_query(F.data.startswith("admin_sub_grant_apply:"))
async def admin_sub_grant_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    tariff_id = int(parts[2])
    days = int(parts[3])

    try:
        user = await get_user_by_telegram_id(
            session,
            telegram_id,
        )
        if not user:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
            return

        if user.is_deleted:
            await callback.answer(
                texts.ADMIN_MANUAL_GRANT_USER_DELETED,
                show_alert=True,
            )
            return

        if user.is_banned:
            await callback.answer(
                texts.ADMIN_MANUAL_GRANT_USER_BANNED,
                show_alert=True,
            )
            return

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer(
                texts.ERROR_TARIFF_NOT_FOUND,
                show_alert=True,
            )
            return

        await SubscriptionService.extend_subscription(
            session,
            telegram_id,
            days,
            new_device_limit=tariff.device_limit,
            new_tariff_id=tariff.id,
        )

        invalidate_user_cache(telegram_id)

        days_text = (
            texts.ADMIN_SUB_PERMANENT_LABEL
            if days >= PERMANENT_SUBSCRIPTION_DAYS
            else f"{days} дн."
        )

        tariff_name = get_tariff_group_name(tariff.device_limit)

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "GRANT",
            "User",
            telegram_id,
            f"{tariff_name} / {days_text}",
        )

        user = await get_user_by_telegram_id(
            session,
            telegram_id,
        )

        new_end_str = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )

        text = texts.ADMIN_SUB_GRANT_SUCCESS.format(
            telegram_id=telegram_id,
            tariff_name=tariff_name,
            days_text=days_text,
            new_end=new_end_str,
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_user_card:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_sub_grant_apply edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_sub_grant_apply error: {e}",
            exc_info=True,
        )
        await session.rollback()
        await callback.answer(
            texts.ADMIN_SUB_GRANT_FAILED,
            show_alert=True,
        )