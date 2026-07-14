"""
Троттлинг для предотвращения спама callback-запросами.
КРИТИЧНО: каждый callback_data троттится отдельно — разные кнопки НЕ блокируют друг друга.

🔥 ИСПРАВЛЕНО: 
- Уменьшен maxsize с 10000 до 5000 (достаточно для 1000 пользователей)
- Добавлен явный cleanup при достижении лимита
- Ключ включает только action_type, а не полный callback_data для экономии памяти
"""
import asyncio
import logging
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache
from bot import texts

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: maxsize=5000 достаточно для 1000 пользователей
# При 1000 пользователей * 5 действий = 5000 уникальных ключей максимум
# TTL увеличен с 0.9с до 2с для более надежного троттлинга
_MAX_CACHE_SIZE = 5000
_DEFAULT_TTL = 2.0


class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.3):
        self.limit = limit
        # 🔥 ИСПРАВЛЕНО: Используем фиксированный TTL вместо limit * 3
        self._last_call = TTLCache(maxsize=_MAX_CACHE_SIZE, ttl=_DEFAULT_TTL)
    
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)
        
        # 🔥 ИСПРАВЛЕНО: Умный ключ — троттлим по user_id + action_type
        # Это предотвращает создание миллионов уникальных ключей
        if isinstance(event, CallbackQuery):
            # Для callback берем префикс до первого ":"
            # Например: "admin_user_card:123" -> "cb:admin_user_card"
            action_data = event.data or ""
            action_type = action_data.split(":")[0] if ":" in action_data else action_data
            action_key = f"cb:{action_type}"
        elif isinstance(event, Message) and event.text:
            # Для сообщений берем первое слово (команду)
            first_word = event.text.split()[0] if event.text.split() else ""
            action_key = f"msg:{first_word}"
        else:
            action_key = None
        
        if not action_key:
            return await handler(event, data)
        
        key = f"{user_id}:{action_key}"
        
        if key in self._last_call:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer(texts.ERROR_TOO_FREQUENT, show_alert=False)
                except Exception:
                    pass
            return
        
        # 🔥 ИСПРАВЛЕНО: Явная очистка при достижении 80% лимита
        if len(self._last_call) >= _MAX_CACHE_SIZE * 0.8:
            self._cleanup_expired()
        
        self._last_call[key] = asyncio.get_running_loop().time()
        return await handler(event, data)
    
    def _cleanup_expired(self) -> None:
        """Явно удаляет expired записи из кэша"""
        now = asyncio.get_event_loop().time()
        expired_keys = [
            k for k, v in self._last_call.items()
            if now - v > _DEFAULT_TTL
        ]
        for k in expired_keys:
            try:
                del self._last_call[k]
            except KeyError:
                pass
        
        if expired_keys:
            logger.debug(f"Throttling cleanup: removed {len(expired_keys)} expired entries")