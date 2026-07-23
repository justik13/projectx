import asyncio
import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from bot import texts
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)

LOCKED_ACTION_PREFIXES = (
    # Создание устройства пользователем.
    "add_device",
    "select_server:",
    "confirm_delete_device:",
    # Админское удаление устройства.
    "admin_delete_device_apply:",
    # Генерация конфигураций.
    "download_conf:",
    # Платежи.
    "pay_sbp:",
    "check_payment:",
    "cancel_invoice:",
    # Админские действия с подпиской.
    "admin_sub_apply_tariff:",
    "admin_sub_apply_extend:",
    "admin_sub_apply_reduce:",
    "admin_sub_grant_apply:",
    # Админские действия с пользователями.
    "admin_ban_apply:",
    "admin_unban_apply:",
    "admin_manual_grant:",
    "admin_manual_grant_apply:",
    # Админские действия с серверами.
    "confirm_server_delete:",
    "admin_server_toggle:",
    "admin_server_toggle_apply:",
    # Админские действия с тарифами.
    "admin_tariff_toggle:",
    "admin_tariff_toggle_apply:",
    "admin_tariff_delete:",
    "admin_tariff_delete_apply:",
    # Режим технических работ.
    "admin_maintenance_toggle",
    "admin_maintenance_toggle_apply",
    # Рассылка.
    "broadcast_send_all",
    "broadcast_send_active",
)


def _is_locked_action(callback_data: str) -> bool:
    if not callback_data:
        return False
    for prefix in LOCKED_ACTION_PREFIXES:
        if callback_data.startswith(prefix) or callback_data == prefix:
            return True
    return False


class ActionLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        callback_data = event.data or ""

        if not _is_locked_action(callback_data):
            return await handler(event, data)

        lock = get_user_action_lock(user_id)

        if lock.locked():
            try:
                await event.answer(
                    texts.ERROR_ACTION_IN_PROGRESS,
                    show_alert=False,
                )
            except Exception:
                pass
            logger.debug(
                "Action blocked for user %d: %s (lock busy)",
                user_id,
                callback_data[:50],
            )
            return

        async with lock:
            return await handler(event, data)