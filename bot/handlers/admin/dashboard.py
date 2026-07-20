import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.dashboard import (
    get_admin_menu,
    get_audit_keyboard,
    get_maintenance_confirm_keyboard,
)
from database.repositories.audit_repo import get_recent_audit_logs
from database.repositories.servers_repo import get_total_free_ips
from database.repositories.users_repo import get_dashboard_stats
from services.audit_service import AuditService
from services.maintenance_service import MaintenanceService
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import render_hub, safe

router = Router()
logger = logging.getLogger(__name__)


async def _render_dashboard(
    callback: CallbackQuery,
    session: AsyncSession,
):
    stats = await get_dashboard_stats(session)

    free_ips = await get_total_free_ips(session)

    maintenance_enabled = False

    if session is not None:
        try:
            maintenance_enabled = await MaintenanceService.is_enabled(
                session
            )
        except Exception as e:
            logger.error(
                "Failed to load maintenance mode state: %s",
                e,
            )

    rendered = texts.DASHBOARD_HEADER + texts.DASHBOARD_STATS.format(
        total_users=stats["total"],
        active_subs=stats["active"],
        new_users_24h=stats["new_24h"],
        free_ips=free_ips,
    )

    if maintenance_enabled:
        rendered += (
            "\n🛠 <b>Технические работы:</b> 🔴 ВКЛЮЧЕНЫ\n"
            "<i>Новые подключения и оплата временно ограничены.</i>\n"
        )
    else:
        rendered += (
            "\n🛠 <b>Технические работы:</b> 🟢 выключены\n"
        )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_admin_menu(maintenance_enabled),
    )


@router.callback_query(F.data == "menu_admin")
async def hub_menu_admin(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    await _render_dashboard(callback, session)

    await callback.answer()


@router.callback_query(F.data == "admin_menu")
async def back_to_admin(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    await _render_dashboard(callback, session)

    await callback.answer()


@router.callback_query(F.data == "admin_audit")
async def show_audit_log(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    logs = await get_recent_audit_logs(session, limit=10)

    if not logs:
        rendered = texts.AUDIT_LOG_HEADER + texts.AUDIT_LOG_EMPTY
    else:
        rendered = texts.AUDIT_LOG_HEADER

        for log in logs:
            action_text = texts.AUDIT_ACTIONS.get(
                log.action,
                log.action,
            )

            target_info = (
                f" {log.target_type} <code>{log.target_id}</code>"
                if log.target_type and log.target_id
                else ""
            )

            details = (
                f"\n<i>{safe(log.details)}</i>"
                if log.details
                else ""
            )

            rendered += texts.AUDIT_ENTRY.format(
                date=format_datetime(log.created_at),
                admin_id=log.admin_id,
                action=action_text,
                target=target_info,
                details=details,
            )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        get_audit_keyboard(),
    )

    await callback.answer()


@router.callback_query(F.data == "admin_maintenance")
async def admin_maintenance_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    maintenance_enabled = False

    if session is not None:
        maintenance_enabled = await MaintenanceService.is_enabled(
            session
        )

    if maintenance_enabled:
        text = (
            "🛠 <b>Режим технических работ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Текущий статус: 🔴 <b>ВКЛЮЧЕН</b>\n\n"
            "Что сейчас ограничено:\n"
            "• создание новых устройств;\n"
            "• создание новых платежей;\n"
            "• выбор тарифа.\n\n"
            "Что продолжает работать:\n"
            "• существующие подключения;\n"
            "• админ-панель;\n"
            "• поддержка;\n"
            "• обработка уже оплаченных платежей.\n\n"
            "⚠️ <b>Выключить режим технических работ?</b>\n"
            "Все ограничения для пользователей будут сняты."
        )
    else:
        text = (
            "🛠 <b>Режим технических работ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Текущий статус: 🟢 <b>ВЫКЛЮЧЕН</b>\n\n"
            "⚠️ <b>Включить режим технических работ?</b>\n\n"
            "Что будет ограничено:\n"
            "• создание новых устройств;\n"
            "• создание новых платежей;\n"
            "• выбор тарифа.\n\n"
            "Что продолжит работать:\n"
            "• существующие подключения;\n"
            "• админ-панель;\n"
            "• поддержка;\n"
            "• обработка уже оплаченных платежей.\n\n"
            "<i>Администраторы могут обходить этот режим.</i>"
        )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_maintenance_confirm_keyboard(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            "admin_maintenance_menu edit_text failed: %s",
            e,
        )

    await callback.answer()


@router.callback_query(F.data == "admin_maintenance_toggle_apply")
async def admin_maintenance_toggle_apply(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    if session is None:
        await callback.answer(
            texts.ERROR_TECHNICAL_ALERT,
            show_alert=True,
        )
        return

    try:
        new_status = await MaintenanceService.toggle(
            session,
            callback.from_user.id,
        )

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "TOGGLE_MAINTENANCE",
            "MaintenanceMode",
            1,
            "enabled" if new_status else "disabled",
        )

        if new_status:
            await callback.answer(
                "✅ Технические работы включены",
                show_alert=True,
            )
        else:
            await callback.answer(
                "✅ Технические работы выключены",
                show_alert=True,
            )

        logger.info(
            "Admin %s toggled maintenance mode to %s",
            callback.from_user.id,
            "enabled" if new_status else "disabled",
        )

    except Exception as e:
        logger.error(
            "Failed to toggle maintenance mode: %s",
            e,
            exc_info=True,
        )

        await callback.answer(
            "❌ Не удалось изменить режим технических работ",
            show_alert=True,
        )

        return

    await _render_dashboard(callback, session)