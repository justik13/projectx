import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_admin_menu, get_audit_keyboard
from bot import texts
from database.repositories.audit_repo import get_recent_audit_logs
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.users_repo import (
    get_active_subscriptions_count,
    get_new_users_count_24h,
    get_user_count,
)
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import safe, safe_delete_message

router = Router()
logger = logging.getLogger(__name__)


async def _render_dashboard(message_or_callback, session: AsyncSession, *, edit: bool = False):
    """Единая функция отрисовки дашборда — для message и callback_query."""
    total_users = await get_user_count(session)
    active_subs = await get_active_subscriptions_count(session)
    new_users_24h = await get_new_users_count_24h(session)
    free_ips = await get_total_free_ips(session)

    rendered = (
        texts.DASHBOARD_HEADER
        + texts.DASHBOARD_STATS.format(
            total_users=total_users,
            active_subs=active_subs,
            new_users_24h=new_users_24h,
            free_ips=free_ips,
        )
    )

    target = message_or_callback.message if isinstance(message_or_callback, CallbackQuery) else message_or_callback

    if edit:
        try:
            await target.edit_text(rendered, reply_markup=get_admin_menu(), parse_mode="HTML")
        except Exception:
            pass
    else:
        await target.answer(rendered, reply_markup=get_admin_menu(), parse_mode="HTML")


@router.message(F.text == "🛠 Админка")
async def show_admin(message: Message, session: AsyncSession = None):
    await safe_delete_message(message)

    if not is_admin(message.from_user.id):
        await message.answer(texts.ERROR_ACCESS_PANEL)
        return

    await _render_dashboard(message, session, edit=False)


@router.callback_query(F.data == "admin_menu")
async def back_to_admin(callback: CallbackQuery, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await _render_dashboard(callback, session, edit=True)
    await callback.answer()


@router.callback_query(F.data == "admin_audit")
async def show_audit_log(callback: CallbackQuery, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    logs = await get_recent_audit_logs(session, limit=10)

    if not logs:
        rendered = texts.AUDIT_LOG_HEADER + texts.AUDIT_LOG_EMPTY
    else:
        rendered = texts.AUDIT_LOG_HEADER
        for log in logs:
            action_text = texts.AUDIT_ACTIONS.get(log.action, log.action)
            target_info = ""
            if log.target_type and log.target_id:
                target_info = f" {log.target_type} <code>{log.target_id}</code>"
            details = f"\n<i>{safe(log.details)}</i>" if log.details else ""

            rendered += texts.AUDIT_ENTRY.format(
                date=format_datetime(log.created_at),
                admin_id=log.admin_id,
                action=action_text,
                target=target_info,
                details=details,
            )

    await callback.message.edit_text(
        rendered,
        reply_markup=get_audit_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()