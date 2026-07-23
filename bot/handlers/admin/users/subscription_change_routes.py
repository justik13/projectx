import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_change_tariff_keyboard,
    get_admin_confirm_action_keyboard,
)
from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.tariffs_repo import get_tariff_by_id
from services.audit_service import AuditService
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.tariff_names import get_tariff_group_name

from .common import (
    _get_representative_tariff,
    _get_tariff_groups,
    _get_user_with_profiles,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_sub_change_tariff:"))
async def admin_sub_change_tariff(
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

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND
        )
        return

    groups = await _get_tariff_groups(session)

    profiles_count = (
        len(user.profiles)
        if user.profiles
        else 0
    )

    current_tariff_name = "—"
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )
        if tariff:
            current_tariff_name = get_tariff_group_name(
                tariff.device_limit
            )

    text = texts.ADMIN_SUB_CHANGE_TARIFF_HEADER.format(
        telegram_id=telegram_id,
        current_tariff=current_tariff_name,
        devices_count=profiles_count,
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_change_tariff_keyboard(
                telegram_id,
                groups,
                user.current_tariff_id,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_sub_change_tariff edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_sub_select_group:"))
async def admin_sub_select_group(
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

    user = await _get_user_with_profiles(session, telegram_id)
    if not user:
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND
        )
        return

    groups = await _get_tariff_groups(session)

    if device_limit not in groups:
        await callback.answer(
            texts.ADMIN_SUB_GROUP_NOT_FOUND,
            show_alert=True,
        )
        return

    tariffs = groups[device_limit]
    new_tariff = _get_representative_tariff(tariffs)

    profiles_count = (
        len(user.profiles)
        if user.profiles
        else 0
    )

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
                reply_markup=get_back_button(
                    f"admin_sub_change_tariff:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                "admin_sub_select_group downgrade "
                f"edit_text failed: {e}"
            )
        return

    if user.current_tariff_id == new_tariff.id:
        await callback.answer(
            texts.ADMIN_SUB_TARIFF_ALREADY_SELECTED,
            show_alert=True,
        )
        return

    old_tariff_name = "—"
    if user.current_tariff_id:
        old_tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )
        if old_tariff:
            old_tariff_name = get_tariff_group_name(
                old_tariff.device_limit
            )

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
                confirm_callback=(
                    f"admin_sub_apply_tariff:"
                    f"{telegram_id}:{new_tariff.id}"
                ),
                cancel_callback=(
                    f"admin_sub_change_tariff:{telegram_id}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            "admin_sub_select_group confirm "
            f"edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_sub_apply_tariff:"))
async def admin_sub_apply_tariff(
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

    try:
        user = await _get_user_with_profiles(
            session,
            telegram_id,
        )
        if not user:
            await callback.message.edit_text(
                texts.ERROR_USER_NOT_FOUND
            )
            return

        new_tariff = await get_tariff_by_id(session, tariff_id)
        if not new_tariff:
            await callback.answer(
                texts.ERROR_TARIFF_NOT_FOUND,
                show_alert=True,
            )
            return

        profiles_count = (
            len(user.profiles)
            if user.profiles
            else 0
        )

        if profiles_count > new_tariff.device_limit:
            text = texts.ADMIN_SUB_DOWNGRADE_BLOCKED.format(
                telegram_id=telegram_id,
                devices_count=profiles_count,
                new_limit=new_tariff.device_limit,
            )

            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_sub_change_tariff:{telegram_id}"
                ),
                parse_mode="HTML",
            )
            return

        await SubscriptionService.extend_subscription(
            session,
            telegram_id,
            days=0,
            new_device_limit=new_tariff.device_limit,
            new_tariff_id=new_tariff.id,
        )

        invalidate_user_cache(telegram_id)

        tariff_name = get_tariff_group_name(
            new_tariff.device_limit
        )

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
                reply_markup=get_back_button(
                    f"admin_user_card:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_sub_apply_tariff edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_sub_apply_tariff error: {e}",
            exc_info=True,
        )
        await session.rollback()
        await callback.answer(
            texts.ADMIN_SUB_CHANGE_FAILED,
            show_alert=True,
        )