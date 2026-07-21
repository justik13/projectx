import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from database.repositories.servers_repo import (
    get_server_by_id,
    update_server,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import safe

from .common import _show_server_card

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(
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

    await state.clear()

    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _show_server_card(callback, session, server)


@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active
    flag = server.country_flag or "🌍"

    if new_status:
        text = (
            "⚠️ <b>Подтверждение включения сервера</b>\n"
            f"{flag} <b>{safe(server.name)}</b>\n"
            "Сервер снова будет доступен пользователям\n"
            "при создании новых устройств.\n"
            "<i>Существующие устройства продолжат работать.</i>"
        )
    else:
        text = (
            "⚠️ <b>Подтверждение отключения сервера</b>\n"
            f"{flag} <b>{safe(server.name)}</b>\n"
            "Сервер будет скрыт из списка доступных локаций\n"
            "при создании новых устройств.\n"
            "<i>Существующие устройства продолжат работать.</i>"
        )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_server_toggle_apply:{server_id}",
                cancel_callback=f"admin_server_card:{server_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_server_confirm edit_text failed: {e}")

    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_toggle_apply:"))
async def toggle_server_apply(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active

    await update_server(
        session,
        server,
        is_active=new_status,
    )

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "TOGGLE_SERVER",
        "Server",
        server_id,
        "enabled" if new_status else "disabled",
    )

    status_text = (
        "включен"
        if new_status
        else "выключен"
    )

    await callback.answer(
        f"✅ Сервер {status_text}",
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} toggled server {server_id} "
        f"to {new_status}"
    )

    refreshed = await get_server_by_id(session, server_id)
    await _show_server_card(callback, session, refreshed)