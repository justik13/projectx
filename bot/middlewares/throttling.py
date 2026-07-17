"""
Троттлинг для предотвращения спама.

🔥 ИСПРАВЛЕНО (Часть 1):
- Global throttle применяется и к Message (не только CallbackQuery)
- Защита от спама текстовыми сообщениями при вводе имени устройства
"""

import logging
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache
from bot import texts

logger = logging.getLogger(__name__)

_MAX_CACHE_SIZE = 5000
_DEFAULT_TTL = 2.0
_GLOBAL_THROTTLE_TTL = 0.3


class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.3):
        self.limit = limit
        self._last_call = TTLCache(maxsize=_MAX_CACHE_SIZE, ttl=_DEFAULT_TTL)
        self._global_throttle = TTLCache(
            maxsize=_MAX_CACHE_SIZE, ttl=_GLOBAL_THROTTLE_TTL
        )

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        # ═══════════════════════════════════════════════════════════
        # 🔥 ИСПРАВЛЕНО: ГЛОБАЛЬНЫЙ PER-USER RATE LIMIT (0.3с)
        # Применяется и к CallbackQuery, и к Message
        # ═══════════════════════════════════════════════════════════
        if isinstance(event, (CallbackQuery, Message)):
            global_key = f"global:{user_id}"
            if global_key in self._global_throttle:
                try:
                    if isinstance(event, CallbackQuery):
                        await event.answer(
                            texts.ERROR_TOO_FREQUENT, show_alert=False
                        )
                    # Для Message просто игнорируем (не спамим ответом)
                except Exception:
                    pass
                return
            self._global_throttle[global_key] = True

        # ═══════════════════════════════════════════════════════════
        # ACTION-TYPE THROTTLING (2.0с для повторных нажатий)
        # ═══════════════════════════════════════════════════════════
        if isinstance(event, CallbackQuery):
            action_data = event.data or ""
            action_type = (
                action_data.split(":")[0] if ":" in action_data else action_data
            )
            action_key = f"cb:{action_type}"
        elif isinstance(event, Message) and event.text:
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
                    await event.answer(
                        texts.ERROR_TOO_FREQUENT, show_alert=False
                    )
                except Exception:
                    pass
            elif isinstance(event, Message):
                logger.debug(
                    f"Throttled message from user {user_id}: {action_key}"
                )
            return

        self._last_call[key] = True
        return await handler(event, data)
