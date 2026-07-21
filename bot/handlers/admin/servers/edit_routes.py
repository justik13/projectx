import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.repositories.servers_repo import (
    get_server_by_id,
    update_server,
)
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_server_edit_name:"))
async def start_edit_server_name(
    callback: CallbackQuery,
    state: FSMContext,
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

    await state.update_data(
        server_id=server_id,
        edit_field="name",
    )

    await state.set_state(AdminStates.editing_server)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_RENAME_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.callback_query(F.data.startswith("admin_server_edit_flag:"))
async def start_edit_server_flag(
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

    current_flag = server.country_flag or "🌍"

    await state.update_data(
        server_id=server_id,
        edit_field="flag",
    )

    await state.set_state(AdminStates.editing_server_flag)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_FLAG_PROMPT_EDIT.format(
            current_flag=safe(current_flag),
        ),
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_flag)
async def process_edit_server_flag(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers"),
        )
        return

    data = await state.get_data()
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )
        await state.clear()
        return

    new_flag = message.text.strip()

    if len(new_flag) > 10:
        await render_hub(
            message.bot,
            message.chat.id,
            "⚠️ Флаг слишком длинный (макс. 10 символов):",
            get_back_button("admin_servers"),
        )
        return

    await update_server(
        session,
        server,
        country_flag=new_flag,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"flag -> {new_flag}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_FLAG_UPDATED.format(flag=safe(new_flag)),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"flag to {new_flag}"
    )

    await state.clear()


@router.message(AdminStates.editing_server)
async def process_edit_server_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers"),
        )
        return

    data = await state.get_data()
    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )
        await state.clear()
        return

    new_name = message.text.strip()

    if len(new_name) > 255:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NAME_TOO_LONG.format(max=255),
            get_back_button("admin_servers"),
        )
        return

    await update_server(
        session,
        server,
        name=new_name,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"name -> {new_name}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_RENAMED.format(
            name=safe(new_name),
        ),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"name to {new_name}"
    )

    await state.clear()