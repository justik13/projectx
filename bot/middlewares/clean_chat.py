import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

class CleanChatMiddleware(BaseMiddleware):
    """
    Удаляет ВСЕ входящие сообщения пользователя (текст, медиа, стикеры, инвойсы).
    Исключение: /start (обрабатывается в start.py).
    Это гарантирует абсолютную чистоту чата.
    """
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.text and event.text.startswith("/start"):
                pass  # start.py сам отрендерит Хаб
            else:
                try:
                    await event.delete()
                except Exception:
                    pass  # Сообщение могло быть уже удалено или это сервисное сообщение
        return await handler(event, data)