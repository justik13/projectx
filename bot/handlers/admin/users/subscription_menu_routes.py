import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import get_admin_subscription_keyboard
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name

from .common import (
    _format_time_left,
    _is_subscription_active,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_subscription:"))
async def admin_subscription_menu(
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
        await callback.message.edit_text(
            texts.ERROR_USER_NOT_FOUND
        )
        return

    has_active = _is_subscription_active(user)

    profiles_count = await get_user_profiles_count(
        session,
        user.id,
    )

    tariff_name = "—"
    device_limit = user.device_limit or 0

    if user.current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )
        if tariff:
            device_limit = tariff.device_limit
            tariff_name = (
                f"{get_tariff_display_name(device_limit)} "
                f"({device_limit} устр.)"
            )

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
            reply_markup=get_admin_subscription_keyboard(
                telegram_id,
                has_active,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_subscription_menu edit_text failed: {e}"
        )