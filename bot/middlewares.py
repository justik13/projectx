from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
import logging

logger = logging.getLogger(__name__)

class UserContextMiddleware(BaseMiddleware):
    """
    🔥 FIX P1: Fetch & Release паттерн.
    Middleware быстро забирает юзера из БД и НЕМЕДЛЕННО закрывает сессию,
    чтобы не блокировать пул соединений SQLite во время долгих сетевых запросов в хэндлерах.
    """
    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        session = await get_session()
        try:
            user = await get_user_by_telegram_id(session, user_id)
            data['db_user'] = user
            
            if user and user.is_banned:
                if isinstance(event, Message):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.", show_alert=True)
                return
            
            if isinstance(event, Message) and event.text and event.text.startswith("/start"):
                return await handler(event, data)
            if isinstance(event, CallbackQuery) and event.data in ["accept_tos", "read_tos"]:
                return await handler(event, data)
            
            if not user or not user.tos_accepted:
                if isinstance(event, Message):
                    await event.answer("📋 Сначала примите пользовательское соглашение.\nНажмите /start")
                elif isinstance(event, CallbackQuery):
                    await event.answer("📋 Сначала примите пользовательское соглашение", show_alert=True)
                return
        except Exception as e:
            logger.error(f"Critical error in UserContextMiddleware: {e}", exc_info=True)
            raise
        finally:
            # 🔥 FIX P1: Закрываем сессию ДО передачи управления хэндлеру
            await session.close()

        return await handler(event, data)