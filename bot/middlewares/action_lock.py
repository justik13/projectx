import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from bot import texts
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)
LOCKED_ACTION_PREFIXES = (
    "add_device",                    # 🔥 ИСПРАВЛЕНО #4+#5: Начало создания устройства
    "select_server:",                # 🔥 ИСПРАВЛЕНО #4+#5: Выбор сервера при создании
    "confirm_delete_device:",        # Подтверждение удаления (user)
    "admin_delete_device_apply:",    # Подтверждение удаления (admin)
    "download_conf:",                # Генерация .vpn + .conf + инструкция
    "pay_stars:",                    # Создание Stars инвойса
    "pay_sbp:",                      # Создание Platega/SBP платежа
    "check_payment:",                # Проверка статуса Platega
    "admin_sub_apply_tariff:",       # Смена тарифа пользователю
    "admin_sub_apply_extend:",       # Продление подписки
    "admin_sub_apply_reduce:",       # Уменьшение дней
    "admin_sub_grant_apply:",        # Выдача доступа
    "admin_ban_apply:",              # Бан пользователя
    "admin_unban_apply:",            # Разбан пользователя
    "confirm_server_delete:",        # Удаление сервера
    "admin_server_toggle:",          # Вкл/выкл сервера
    "admin_tariff_toggle:",          # Вкл/выкл тарифа
    "broadcast_send_all",            # Отправить всем
    "broadcast_send_active",         # Отправить активным
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