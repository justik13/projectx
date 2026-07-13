"""
Троттлинг для предотвращения спама callback-запросами.
Каждый callback_data троттится отдельно — разные кнопки НЕ блокируют друг друга.
"""
import asyncio
import logging
from aiogram.types import CallbackQuery
from cachetools import TTLCache
from bot import texts

logger = logging.getLogger(__name__)


class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.3):
        """
        :param limit: минимальный интервал между одинаковыми callback_data (секунды)
        """
        self.limit = limit
        # TTL = 3x limit для надёжного троттлинга
        self._last_call = TTLCache(maxsize=10000, ttl=limit * 3)
    
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        
        # Для callback используем UNIQUE ключ = callback_data
        # РАЗНЫЕ кнопки НЕ блокируют друг друга!
        if isinstance(event, CallbackQuery):
            action_key = f"cb:{event.data}"
        elif hasattr(event, "text"):
            action_key = f"msg:{event.text or ''}"
        else:
            action_key = None
        
        if not action_key:
            return await handler(event, data)
        
        key = f"{user_id}:{action_key}"
        
        if key in self._last_call:
            # Этот callback_data уже был вызван недавно
            if isinstance(event, CallbackQuery):
                try:
                    # Тихий ответ без alert — не раздражать пользователя
                    await event.answer()
                except Exception:
                    pass
            return  # Пропускаем обработчик
        
        # Записываем время вызова
        self._last_call[key] = asyncio.get_running_loop().time()
        return await handler(event, data)