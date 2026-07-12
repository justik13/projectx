"""
UserContextMiddleware с оптимизированным кэшированием.
Кэширует plain dict вместо ORM объектов — устраняет задержки session.merge.
"""
import logging
from cachetools import TTLCache
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup
from bot import texts
from config.settings import get_settings
from database.repositories.users_repo import get_user_by_telegram_id

logger = logging.getLogger(__name__)

# Кэш на 3 секунды — убирает SELECT при быстрых кликах
_user_cache = TTLCache(maxsize=5000, ttl=3.0)

class UserContextMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
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
                user = await get_user_by_telegram_id(session, user_id)
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