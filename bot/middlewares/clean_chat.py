import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

class CleanChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            # Всегда удаляем все сообщения, включая /start
            try:
                await event.delete()
            except Exception:
                pass  # Сообщение могло быть уже удалено или это сервисное сообщение
        
        return await handler(event, data)