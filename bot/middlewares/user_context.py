"""
UserContextMiddleware — загрузка пользователя в контекст.
🔥 ИСПРАВЛЕНО (Этап 2): Использование session.merge() вместо ручного конструирования User.
"""
import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime
from cachetools import TTLCache
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from bot import texts
from bot.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL
from config.settings import get_settings
from database.models import User

logger = logging.getLogger(__name__)

@dataclass
class UserDTO:
    """DTO для кэширования пользователя."""
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None
    tos_accepted: bool
    subscription_end: datetime | None
    device_limit: int
    current_tariff_id: int | None
    referred_by: int | None
    referral_days: int
    last_payment_at: datetime | None
    is_banned: bool
    is_admin: bool
    is_bot_blocked: bool
    created_at: datetime

    @classmethod
    def from_orm(cls, user: User) -> "UserDTO":
        return cls(
            id=user.id, telegram_id=user.telegram_id,
            username=user.username, first_name=user.first_name,
            tos_accepted=user.tos_accepted, subscription_end=user.subscription_end,
            device_limit=user.device_limit, current_tariff_id=user.current_tariff_id,
            referred_by=user.referred_by, referral_days=user.referral_days,
            last_payment_at=user.last_payment_at, is_banned=user.is_banned,
            is_admin=user.is_admin, is_bot_blocked=user.is_bot_blocked,
            created_at=user.created_at,
        )

_user_cache: TTLCache[int, UserDTO] = TTLCache(
    maxsize=USER_CONTEXT_CACHE_MAX_SIZE, ttl=USER_CONTEXT_CACHE_TTL
)
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 300.0

def _maybe_cleanup_cache() -> None:
    global _last_cleanup_time
    now = asyncio.get_event_loop().time()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL:
        return
    _last_cleanup_time = now
    if len(_user_cache) >= USER_CONTEXT_CACHE_MAX_SIZE * 0.8:
        expired_keys = []
        for key in list(_user_cache.keys()):
            try:
                _ = _user_cache[key]
            except KeyError:
                expired_keys.append(key)
        for key in expired_keys:
            try:
                del _user_cache[key]
            except KeyError:
                pass

class UserContextMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        _maybe_cleanup_cache()
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        if not user_id:
            return await handler(event, data)

        session = data.get("session")
        if not session:
            return await handler(event, data)

        try:
            user = None
            if user_id in _user_cache:
                user_dto = _user_cache[user_id]
                needs_update = False
                update_data = {}
                if user_dto.is_bot_blocked:
                    update_data['is_bot_blocked'] = False
                    needs_update = True
                current_username = event.from_user.username
                if user_dto.username != current_username:
                    update_data['username'] = current_username
                    needs_update = True
                current_first_name = event.from_user.first_name
                if user_dto.first_name != current_first_name:
                    update_data['first_name'] = current_first_name
                    needs_update = True

                if needs_update:
                    try:
                        await session.execute(
                            update(User).where(User.telegram_id == user_id).values(**update_data)
                        )
                        await session.flush()
                        for k, v in update_data.items():
                            setattr(user_dto, k, v)
                    except Exception as e:
                        logger.error(f"Failed to update user context in DB: {e}")

                # 🔥 ИСПРАВЛЕНО (Этап 2): Используем session.merge() для создания attached объекта
                # Раньше: user = User(id=..., username=...) — создавал Detached Instance без связей
                # Теперь: merge() привязывает объект к сессии, сохраняя скалярные поля
                detached_user = User(
                    id=user_dto.id, telegram_id=user_dto.telegram_id,
                    username=user_dto.username, first_name=user_dto.first_name,
                    tos_accepted=user_dto.tos_accepted, subscription_end=user_dto.subscription_end,
                    device_limit=user_dto.device_limit, current_tariff_id=user_dto.current_tariff_id,
                    referred_by=user_dto.referred_by, referral_days=user_dto.referral_days,
                    last_payment_at=user_dto.last_payment_at, is_banned=user_dto.is_banned,
                    is_admin=user_dto.is_admin, is_bot_blocked=user_dto.is_bot_blocked,
                    created_at=user_dto.created_at,
                )
                user = await session.merge(detached_user)
            else:
                stmt = (
                    select(User)
                    .where(User.telegram_id == user_id)
                    .options(
                        selectinload(User.current_tariff),
                        selectinload(User.profiles),
                        selectinload(User.payments),
                    )
                )
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if user:
                    user_dto = UserDTO.from_orm(user)
                    _user_cache[user_id] = user_dto

            if user:
                data["db_user"] = user
                if user.is_banned:
                    support_username = get_settings().SUPPORT_USERNAME.lstrip("@")
                    kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text=f"💬 Поддержка @{support_username}",
                            url=f"https://t.me/{support_username}"
                        )
                    ]])
                    if isinstance(event, Message):
                        await event.answer(texts.ERROR_BANNED_MESSAGE, reply_markup=kb)
                    elif isinstance(event, CallbackQuery):
                        await event.answer(texts.ERROR_BANNED_ALERT, show_alert=True)
                    return
        except Exception as e:
            logger.error(f"UserContextMiddleware error: {e}", exc_info=True)

        return await handler(event, data)