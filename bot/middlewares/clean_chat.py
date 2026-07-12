"""
Middleware для абсолютной чистоты чата.
Удаляет ВСЕ входящие сообщения пользователя (текст, медиа, стикеры),
кроме команды /start (она обрабатывается отдельно).
"""
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

class CleanChatMiddleware(BaseMiddleware):
    """Удаляет входящие сообщения пользователя для чистоты чата."""
    
    async def __call__(self, handler, event, data):
        # Обрабатываем только Message (не CallbackQuery)
        if isinstance(event, Message):
            # Не удаляем команду /start — она обработается в start.py
            if event.text and event.text.startswith("/start"):
                pass  # start.py сам удалит
            else:
                # Удаляем сообщение пользователя
                try:
                    await event.delete()
                except Exception:
                    pass  # Сообщение могло быть уже удалено
        
        return await handler(event, data)