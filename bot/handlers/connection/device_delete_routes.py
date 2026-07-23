from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_device_delete_confirm_keyboard,
    get_device_keyboard,
)
from database.models import User
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import DeviceService
from utils.telegram import render_hub, safe

from .common import _render_connections

router = Router()

_deleting_devices: set[int] = set()


@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_DELETE_CONFIRM.format(
            device_name=safe(profile.device_name),
        ),
        get_device_delete_confirm_keyboard(profile_id),
    )


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer(texts.DEVICE_DELETE_CANCELLED)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_MANAGE_TITLE,
        get_device_keyboard(profile_id),
    )


@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    profile_id = int(callback.data.split(":")[1])

    if profile_id in _deleting_devices:
        await callback.answer(
            texts.DEVICE_DELETE_IN_PROGRESS,
            show_alert=True,
        )
        return

    _deleting_devices.add(profile_id)

    try:
        await callback.answer(texts.DEVICE_DELETING_PROGRESS)
        await state.clear()

        profile = await get_profile_by_id(session, profile_id)
        if (
            not profile
            or not db_user
            or profile.user_id != db_user.id
        ):
            await callback.answer(
                texts.ERROR_ACCESS_DENIED,
                show_alert=True,
            )
            return

        if not await DeviceService.delete_device(
            session,
            profile,
            actor_id=callback.from_user.id,
        ):
            await callback.answer(
                texts.ERROR_SERVER_UNAVAILABLE_GENERIC,
                show_alert=True,
            )
            return

        user = db_user or await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )

        if user:
            await _render_connections(
                callback.message,
                user,
                session,
            )

    finally:
        _deleting_devices.discard(profile_id)