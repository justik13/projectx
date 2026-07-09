from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
import logging

logger = logging.getLogger(__name__)

class UserContextMiddleware(BaseMiddleware):
    """Единое middleware для получения контекста пользователя и проверки доступа"""
    
    async def __call__(self, handler, event, data):
        # Получаем user_id
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        # Если событие не от пользователя (например, системный update), пропускаем
        if not user_id:
            return await handler(event, data)

        session = await get_session()
        try:
            # 1. Получаем пользователя за один запрос
            user = await get_user_by_telegram_id(session, user_id)
            data['db_user'] = user

            # 2. Проверяем, что пользователь не заблокирован (ПРИОРИТЕТ 1)
            if user and user.is_banned:
                if isinstance(event, Message):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.", show_alert=True)
                return  # Прерываем цепочку, сессия закроется в finally
                
            # 3. Разрешаем /start и принятие оферты даже без принятой оферты (ПРИОРИТЕТ 2)
            if isinstance(event, Message) and event.text and event.text.startswith("/start"):
                return await handler(event, data)
                
            if isinstance(event, CallbackQuery) and event.data in ["accept_tos", "read_tos"]:
                return await handler(event, data)
                
            # 4. Проверяем, что пользователь принял оферту
            if not user or not user.tos_accepted:
                if isinstance(event, Message):
                    await event.answer("📋 Сначала примите пользовательское соглашение.\n\nНажмите /start")
                elif isinstance(event, CallbackQuery):
                    await event.answer("📋 Сначала примите пользовательское соглашение", show_alert=True)
                return  # Прерываем цепочку
                
            # 5. Все проверки пройдены — передаем управление хэндлеру ВНУТРИ try!
            # Это гарантирует, что сессия БД открыта, пока хэндлер работает с объектом user
            return await handler(event, data)
            
        except Exception as e:
            logger.error(f"Critical error in UserContextMiddleware: {e}", exc_info=True)
            raise
        finally:
            # Сессия закроется только ПОСЛЕ того, как хэндлер полностью отработает
            await session.close()