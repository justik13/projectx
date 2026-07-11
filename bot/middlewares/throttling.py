import asyncio
from cachetools import TTLCache
from aiogram.types import CallbackQuery


class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.5):
        self.limit = limit
        self._last_call = TTLCache(maxsize=10000, ttl=limit * 3)

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        # 🔥 ИСПРАВЛЕНО: Для CallbackQuery используем глобальный ключ
        # Это предотвращает Race Conditions при быстром клике по разным кнопкам
        if isinstance(event, CallbackQuery):
            action_key = "callback"
        elif hasattr(event, 'text'):
            action_key = f"msg:{event.text or ''}"
        else:
            action_key = None

        if not action_key:
            return await handler(event, data)

        key = f"{user_id}:{action_key}"
        if key in self._last_call:
            if hasattr(event, 'answer'):
                try:
                    await event.answer("⏳ Слишком часто!", show_alert=False)
                except Exception:
                    pass
            return
        
        self._last_call[key] = asyncio.get_running_loop().time()
        return await handler(event, data)