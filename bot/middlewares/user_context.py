"""
UserContextMiddleware — ЛЁГКИЙ middleware.
Единственная задача: загрузить User из БД и положить в data['db_user'].

НЕ делает:
  - UPDATE в БД (синхронизация username)
  - Проверку бана (это BanCheckMiddleware)
  - Отправку сообщений
  - Очистку кэша (cachetools делает это автоматически)
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL
from database.models import User

logger = logging.getLogger(__name__)

# Кэш: telegram_id -> User | None. TTL из конфига.
# cachetools сам инвалидирует просроченные ключи при обращении.
_user_cache: TTLCache[int, User | None] = TTLCache(
    maxsize=USER_CONTEXT_CACHE_MAX_SIZE, 
    ttl=USER_CONTEXT_CACHE_TTL
)

# Sentinel для различения "ключ отсутствует" и "значение None"
_SENTINEL = object()


def invalidate_user_cache(telegram_id: int) -> None:
    """Вызывать после UPDATE юзера (бан, продление, смена тарифа)."""
    _user_cache.pop(telegram_id, None)


class UserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        # Определяем telegram_id
        telegram_id: int | None = None
        if isinstance(event, Message) and event.from_user:
            telegram_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            telegram_id = event.from_user.id

        if telegram_id is None:
            data["db_user"] = None
            return await handler(event, data)

        # Пробуем кэш
        cached = _user_cache.get(telegram_id, _SENTINEL)
        if cached is not _SENTINEL:
            data["db_user"] = cached
            return await handler(event, data)

        # Загрузка из БД
        session: AsyncSession | None = data.get("session")
        if session is None:
            data["db_user"] = None
            return await handler(event, data)

        stmt = select(User).where(
            User.telegram_id == telegram_id,
            User.is_deleted == False,
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        # Кэшируем (включая None — чтобы не долбить БД на каждый запрос нового юзера)
        _user_cache[telegram_id] = user
        data["db_user"] = user

        return await handler(event, data)