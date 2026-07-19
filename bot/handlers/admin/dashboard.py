import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_admin_menu, get_audit_keyboard
from bot import texts
from database.repositories.audit_repo import get_recent_audit_logs
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.users_repo import get_dashboard_stats
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import safe, render_hub

router = Router()
logger = logging.getLogger(__name__)


async def _render_dashboard(callback: CallbackQuery, session: AsyncSession):
    stats = await get_dashboard_stats(session)
    free_ips = await get_total_free_ips(session)

    rendered = texts.DASHBOARD_HEADER + texts.DASHBOARD_STATS.format(
        total_users=stats["total"],
        active_subs=stats["active"],
        new_users_24h=stats["new_24h"],
        free_ips=free_ips,
    )

    await render_hub(callback.bot, callback.message.chat.id, rendered, get_admin_menu())


@router.callback_query(F.data == "menu_admin")
async def hub_menu_admin(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await _render_dashboard(callback, session)
    await callback.answer()


@router.callback_query(F.data == "admin_menu")
async def back_to_admin(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await _render_dashboard(callback, session)
    await callback.answer()


@router.callback_query(F.data == "admin_audit")
async def show_audit_log(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()

    logs = await get_recent_audit_logs(session, limit=10)

    if not logs:
        rendered = texts.AUDIT_LOG_HEADER + texts.AUDIT_LOG_EMPTY
    else:
        rendered = texts.AUDIT_LOG_HEADER
        for log in logs:
            action_text = texts.AUDIT_ACTIONS.get(log.action, log.action)
            target_info = f" {log.target_type} <code>{log.target_id}</code>" if log.target_type and log.target_id else ""
            details = f"\n<i>{safe(log.details)}</i>" if log.details else ""
            rendered += texts.AUDIT_ENTRY.format(
                date=format_datetime(log.created_at),
                admin_id=log.admin_id,
                action=action_text,
                target=target_info,
                details=details
            )

    await render_hub(callback.bot, callback.message.chat.id, rendered, get_audit_keyboard())
    await callback.answer()