import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import (
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
)
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.servers_repo import get_server_by_id
from services.device_service import DeviceService
from utils.admin import is_admin
from utils.telegram import safe

from .common import _get_user_with_profiles

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_user_devices:"))
async def admin_user_devices(
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
            "❌ Пользователь не найден."
        )
        return

    profiles = user.profiles if user.profiles else []

    if not profiles:
        text = (
            texts.ADMIN_USER_DEVICES_HEADER.format(
                telegram_id=telegram_id
            )
            + "\n"
            + texts.ADMIN_USER_DEVICES_EMPTY
        )
    else:
        text = texts.ADMIN_USER_DEVICES_HEADER.format(
            telegram_id=telegram_id
        )

        for profile in profiles:
            name = (
                getattr(profile, "device_name", None)
                or f"Устройство #{profile.id}"
            )

            text += f"\n• {safe(name)}"

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_user_devices_keyboard(
                telegram_id,
                profiles,
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_user_devices edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_delete_device:"))
async def admin_delete_device_confirm(
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
    profile_id = int(parts[2])

    profile = await get_profile_by_id(session, profile_id)

    if not profile:
        await callback.answer(
            texts.ERROR_PROFILE_NOT_FOUND,
            show_alert=True,
        )
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
                confirm_callback=(
                    f"admin_delete_device_apply:"
                    f"{telegram_id}:{profile_id}"
                ),
                cancel_callback=(
                    f"admin_user_devices:{telegram_id}"
                ),
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_delete_device_confirm edit_text failed: {e}"
        )


@router.callback_query(F.data.startswith("admin_delete_device_apply:"))
async def admin_delete_device_apply(
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
    profile_id = int(parts[2])

    try:
        profile = await get_profile_by_id(session, profile_id)

        if not profile:
            await callback.answer(
                texts.ERROR_PROFILE_NOT_FOUND,
                show_alert=True,
            )
            return

        device_name = profile.device_name

        #
        # actor_id нужен для корректного аудита.
        #
        # Здесь удаление выполняет админ,
        # поэтому actor_id = callback.from_user.id.
        #
        # Дополнительный ручной AuditService.log_action здесь не нужен,
        # потому что DeviceService.delete_device уже пишет аудит
        # DEVICE_DELETED с правильным actor_id.
        #
        success = await DeviceService.delete_device(
            session,
            profile,
            actor_id=callback.from_user.id,
        )

        if not success:
            await callback.answer(
                "⚠️ Не удалось удалить устройство. "
                "Сервер недоступен.",
                show_alert=True,
            )
            return

        text = texts.ADMIN_DELETE_DEVICE_SUCCESS.format(
            telegram_id=telegram_id,
            device_name=safe(device_name),
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_user_devices:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_delete_device_apply edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_delete_device_apply error: {e}",
            exc_info=True,
        )

        await session.rollback()

        await callback.answer(
            "❌ Ошибка при удалении устройства",
            show_alert=True,
        )