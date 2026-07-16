"""
BanCheckMiddleware — лёгкий middleware, который проверяет бан
ПОСЛЕ загрузки пользователя (UserContextMiddleware) и ДО хендлера.

Принцип: middleware не делает UPDATE, не шлёт сообщений в чат,
а только прерывает цепочку с коротким alert.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from bot import texts

logger = logging.getLogger(__name__)


class BanCheckMiddleware(BaseMiddleware):
    """
    Перехватывает запрос от забаненного пользователя.
    Работает только если data['db_user'] уже загружен (UserContextMiddleware отработал).
    """

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        db_user = data.get("db_user")

        # Если юзер не загружен (например, /start нового юзера) — пропускаем
        if db_user is None:
            return await handler(event, data)

        # Проверка бана
        if getattr(db_user, "is_banned", False):
            logger.info(
                "Banned user %s attempted action: %s",
                db_user.telegram_id,
                type(event).__name__,
            )
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(texts.ERROR_BANNED_ALERT, show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer(texts.ERROR_BANNED_MESSAGE)
                except Exception:
                    pass
            # Прерываем цепочку — хендлер НЕ выполняется
            return None

        return await handler(event, data)