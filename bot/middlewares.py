from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from config.settings import get_settings
from typing import Callable, Dict, Any, Awaitable
import logging

class BanCheckMiddleware(BaseMiddleware):
    """Проверяет, забанен ли пользователь"""
    
    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        
        if user_id:
            session = await get_session()
            user = await get_user_by_telegram_id(session, user_id)
            if user and user.is_banned:
                if isinstance(event, Message):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.", show_alert=True)
                return
            await session.close()
        
        return await handler(event, data)
