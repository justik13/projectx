"""
ActionLockMiddleware — эксклюзивная блокировка «тяжёлых» действий.
Предотвращает race conditions при:
- Одновременном нажатии разных кнопок (оплата Stars + SBP)
- Double-click на кнопку удаления + скачивания конфига
- Параллельных админских действиях (toggle server + delete device)
- Спаме создания устройств (add_device + select_server)

Принцип работы:
1. Проверяет callback_data на наличие в LOCKED_ACTION_PREFIXES
2. Если действие «тяжёлое» — пытается захватить per-user asyncio.Lock
3. Если lock уже занят — отвечает «⏳» и прерывает выполнение
4. Если lock свободен — захватывает и вызывает handler

Безопасные действия (навигация, рендер) НЕ блокируются.
"""
import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from bot import texts
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)

# Паттерны callback_data, требующие эксклюзивного доступа.
# Если callback_data начинается с одного из этих префиксов (или равен ему) —
# захватывается per-user asyncio.Lock.
LOCKED_ACTION_PREFIXES = (
    # ── Устройства ──
    "add_device",                    # 🔥 ИСПРАВЛЕНО #4+#5: Начало создания устройства
    "select_server:",                # 🔥 ИСПРАВЛЕНО #4+#5: Выбор сервера при создании
    "confirm_delete_device:",        # Подтверждение удаления (user)
    "admin_delete_device_apply:",    # Подтверждение удаления (admin)

    # ── Скачивание конфигов ──
    "download_conf:",                # Генерация .vpn + .conf + инструкция

    # ── Оплата ──
    "pay_stars:",                    # Создание Stars инвойса
    "pay_sbp:",                      # Создание Platega/SBP платежа
    "check_payment:",                # Проверка статуса Platega

    # ── Админка: применение изменений ──
    "admin_sub_apply_tariff:",       # Смена тарифа пользователю
    "admin_sub_apply_extend:",       # Продление подписки
    "admin_sub_apply_reduce:",       # Уменьшение дней
    "admin_sub_grant_apply:",        # Выдача доступа
    "admin_ban_apply:",              # Бан пользователя
    "admin_unban_apply:",            # Разбан пользователя
    "confirm_server_delete:",        # Удаление сервера
    "admin_server_toggle:",          # Вкл/выкл сервера
    "admin_tariff_toggle:",          # Вкл/выкл тарифа

    # ── Рассылка ──
    "broadcast_send_all",            # Отправить всем
    "broadcast_send_active",         # Отправить активным
)


def _is_locked_action(callback_data: str) -> bool:
    """
    Проверяет, требует ли действие эксклюзивной блокировки.
    Сравнивает callback_data с каждым префиксом:
    - startswith(prefix) — для действий с параметрами (pay_stars:123)
    - == prefix — для действий без параметров (broadcast_send_all)
    """
    if not callback_data:
        return False
    for prefix in LOCKED_ACTION_PREFIXES:
        if callback_data.startswith(prefix) or callback_data == prefix:
            return True
    return False


class ActionLockMiddleware(BaseMiddleware):
    """
    Middleware для предотвращения race conditions.
    Для «тяжёлых» действий (создание, удаление, оплата, скачивание)
    захватывает per-user asyncio.Lock. Если lock уже занят другим
    действием — отвечает «⏳ Выполняется предыдущее действие...»
    и не вызывает handler.

    Для безопасных действий (навигация, рендер, выбор) — не блокирует.
    Работает ТОЛЬКО для CallbackQuery (не для Message).
    """

    async def __call__(self, handler, event, data):
        # Работаем только с callback_query
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        callback_data = event.data or ""

        # Безопасное действие (навигация) — пропускаем без lock
        if not _is_locked_action(callback_data):
            return await handler(event, data)

        # Тяжёлое действие — пытаемся захватить lock
        lock = get_user_action_lock(user_id)
        if lock.locked():
            # Lock уже занят другим действием этого пользователя
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

        # Захватываем lock и выполняем handler
        # Между lock.locked() и async with lock нет await,
        # поэтому race condition невозможен (single-threaded event loop)
        async with lock:
            return await handler(event, data)