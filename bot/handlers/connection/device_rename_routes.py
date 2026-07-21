from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button, get_device_keyboard
from bot.states import DeviceManagementStates
from database.models import User
from database.repositories.profiles_repo import (
    get_profile_by_id,
    update_profile,
)
from services.subscription import SubscriptionService
from utils.telegram import render_hub, safe

from .common import DEVICE_NAME_REGEX

router = Router()


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()

    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await callback.answer(
            "⚠️ Доступ неактивен. Продлите подписку.",
            show_alert=True,
        )
        return

    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_RENAME_PROMPT,
        get_back_button(f"manage_device:{profile_id}"),
    )


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    if not message.text or message.text.startswith("/"):
        await state.clear()
        return

    data = await state.get_data()
    profile_id = data.get("profile_id")

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await state.clear()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_ACCESS_DENIED,
            get_back_button("back_to_connections"),
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await state.clear()

        await render_hub(
            message.bot,
            message.chat.id,
            "⚠️ Доступ неактивен. Продлите подписку.",
            get_back_button("back_to_connections"),
        )
        return

    new_name = message.text.strip()

    if (
        not new_name
        or len(new_name) > 16
        or not DEVICE_NAME_REGEX.match(new_name)
    ):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_INVALID_DEVICE_NAME,
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    await update_profile(
        session,
        profile,
        device_name=new_name,
    )

    await render_hub(
        message.bot,
        message.chat.id,
        f"✅ Устройство переименовано в <b>{safe(new_name)}</b>",
        get_device_keyboard(profile.id),
    )

    await state.clear()