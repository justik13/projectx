"""
UserContextMiddleware — загрузка пользователя в контекст.
Загружает User с eager loading нужных relationship для избежания DetachedInstanceError.

🔥 ИСПРАВЛЕНО:
- Уменьшен maxsize кэша с 5000 до 2000 (достаточно для 1000 активных пользователей)
- Увеличен TTL с 3с до 5с для снижения нагрузки на БД
- Добавлена периодическая очистка expired записей
"""
import logging
import asyncio
from cachetools import TTLCache
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from bot import texts
from bot.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL
from config.settings import get_settings
from database.models import User

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: Оптимизированные параметры кэша
_user_cache = TTLCache(maxsize=USER_CONTEXT_CACHE_MAX_SIZE, ttl=USER_CONTEXT_CACHE_TTL)
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 300.0  # Очищать раз в 5 минут


def _maybe_cleanup_cache() -> None:
    """Периодическая очистка expired записей из кэша"""
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
        
        if expired_keys:
            logger.debug(f"User cache cleanup: {len(expired_keys)} expired entries removed")


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
            # Проверяем кэш
            if user_id in _user_cache:
                user = _user_cache[user_id]
            else:
                # ✅ ИСПРАВЛЕНО: User.current_tariff (не User.tariff)
                # eager loading prevents DetachedInstanceError
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
                    _user_cache[user_id] = user
            
            data["db_user"] = user
            
            # Проверка бана
            if user and user.is_banned:
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
                
                return  # Обрываем цепочку
            
        except Exception as e:
            logger.error(f"UserContextMiddleware error: {e}", exc_info=True)
        
        return await handler(event, data)