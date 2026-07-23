from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache

from bot import texts
from config.settings import get_settings

logger = logging.getLogger(__name__)

_ban_alert_cache: TTLCache[int, bool] = TTLCache(maxsize=10000, ttl=300.0)


class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id if event.from_user else None

        #
        # Владелец/админ из ADMIN_IDS не должен блокироваться ban check.
        #
        # Это защита от lockout, если в БД случайно окажется is_banned=True
        # для администратора.
        #
        if user_id is not None:
            settings = get_settings()
            if user_id in settings.ADMIN_IDS:
                return await handler(event, data)

        db_user = data.get("db_user")
        if db_user is None:
            return await handler(event, data)

        if getattr(db_user, "is_banned", False):
            if user_id and user_id not in _ban_alert_cache:
                _ban_alert_cache[user_id] = True
                logger.info(
                    "Banned user %s attempted action: %s (alert sent)",
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
        else:
            logger.debug(
                "Banned user %s attempted action: %s (alert throttled)",
                db_user.telegram_id if db_user else "unknown",
                type(event).__name__,
            )
            return None

        return None