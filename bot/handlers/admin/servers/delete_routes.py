import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.servers import get_server_delete_confirm_keyboard
from bot.states import AdminStates
from database.connection import queue_post_commit_task
from database.models import VPNProfile
from database.repositories.servers_repo import (
    delete_profiles_by_server_id,
    delete_server,
    get_server_by_id,
)
from services.amnezia_client import cleanup_server_circuit_breakers
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import safe

from .common import _delete_server_background, _show_servers_list

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_server_delete:"))
async def request_delete_server(
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

    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    result = await session.execute(
        select(VPNProfile.id).where(
            VPNProfile.server_id == server.id
        ),
    )

    profiles_count = len(result.all())
    flag = server.country_flag or "🌍"

    await state.update_data(delete_server_id=server_id)
    await state.set_state(AdminStates.confirming_server_delete)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_DELETE_CONFIRM.format(
            flag=flag,
            name=safe(server.name),
            profiles_count=profiles_count,
        ),
        reply_markup=get_server_delete_confirm_keyboard(server_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("confirm_server_delete:"))
async def confirm_delete_server(
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

    current_state = await state.get_state()
    if current_state != AdminStates.confirming_server_delete:
        await callback.answer(
            "⚠️ Сессия подтверждения истекла",
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
        await _show_servers_list(callback, session, page=1)
        return

    server_name = server.name
    api_url = server.api_url
    api_key = server.api_key

    result = await session.execute(
        select(
            VPNProfile.id,
            VPNProfile.peer_id,
        ).where(
            VPNProfile.server_id == server.id
        ),
    )

    profiles_data = result.all()

    deleted_profiles = await delete_profiles_by_server_id(
        session,
        server_id,
    )

    await delete_server(session, server)

    cleanup_server_circuit_breakers(api_url)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "DELETE_SERVER",
        "Server",
        server_id,
        f"{server_name}: {deleted_profiles} profiles deleted",
    )

    await callback.answer(
        f"✅ Сервер {server_name} удалён ({deleted_profiles} устр.)",
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} fully deleted server {server_id} "
        f"({server_name}) with {deleted_profiles} profiles"
    )

    await _show_servers_list(callback, session, page=1)

    if profiles_data:
        queue_post_commit_task(
            session,
            lambda bot=callback.bot,
            admin_id=callback.from_user.id,
            srv_name=server_name,
            data=profiles_data,
            url=api_url,
            key=api_key,
            deleted=deleted_profiles: (
                _delete_server_background(
                    bot,
                    admin_id,
                    srv_name,
                    data,
                    url,
                    key,
                    deleted,
                )
            ),
        )