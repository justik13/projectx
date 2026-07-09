from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from typing import Callable, Dict, Any, Awaitable


class UserContextMiddleware(BaseMiddleware):
    """Единое middleware для получения контекста пользователя и проверки доступа"""
    
    async def __call__(self, handler, event, data):
        # Получаем user_id
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id:
            session = await get_session()
            try:
                # Получаем пользователя за один запрос
                user = await get_user_by_telegram_id(session, user_id)
                
                # Сохраняем пользователя в данные для доступа из обработчиков
                data['db_user'] = user

                # Проверяем, что пользователь не заблокирован (ПРИОРИТЕТ 1)
                if user and user.is_banned:
                    if isinstance(event, Message):
                        await event.answer("⛔️ У вас заблокирован доступ к сервису.")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⛔️ У вас заблокирован доступ к сервису.", show_alert=True)
                    return

                # Проверяем, что команда /start или callback'и принятия оферты всегда работают (ПРИОРИТЕТ 2)
                if isinstance(event, Message) and event.text and event.text.startswith("/start"):
                    return await handler(event, data)

                if isinstance(event, CallbackQuery):
                    if event.data in ["accept_tos", "read_tos"]:
                        return await handler(event, data)
                
                # Проверяем, что пользователь принял оферту
                if not user or not user.tos_accepted:
                    if isinstance(event, Message):
                        await event.answer("📋 Сначала примите пользовательское соглашение.\n\nНажмите /start")
                    elif isinstance(event, CallbackQuery):
                        await event.answer("📋 Сначала примите пользовательское соглашение", show_alert=True)
                    return
                    
            finally:
                await session.close()
        
        return await handler(event, data)
