"""
Троттлинг для предотвращения спама callback-запросами.
Два уровня защиты:
1. ГЛОБАЛЬНЫЙ per-user limit (0.3с) — блокирует ВСЕ callback_query подряд
   независимо от типа. Предотвращает «2 кнопки одновременно».
2. ACTION_TYPE limit (2.0с) — блокирует повторное нажатие ТОЙ ЖЕ кнопки.
   Предотвращает double-click.

КРИТИЧНО: каждый callback_data троттится отдельно — разные кнопки НЕ блокируют
друг друга на уровне action_type, но глобальный limit закрывает этот gap.

🔥 ИСПРАВЛЕНО:
- Убрана ручная очистка кэша (_cleanup_expired)
- cachetools.TTLCache сам удаляет просроченные ключи при обращении
- Добавлен глобальный per-user rate limit (TTLCache 0.3с)
- Уменьшен maxsize с 10000 до 5000 (достаточно для 1000 пользователей)
"""
import logging
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache
from bot import texts

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: maxsize=5000 достаточно для 1000 пользователей
# При 1000 пользователей * 5 действий = 5000 уникальных ключей максимум
_MAX_CACHE_SIZE = 5000
_DEFAULT_TTL = 2.0
_GLOBAL_THROTTLE_TTL = 0.3  # НОВОЕ: 0.3с между любыми callback_query


class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.3):
        self.limit = limit
        # Action-type throttling (2.0с для повторных нажатий той же кнопки)
        self._last_call = TTLCache(maxsize=_MAX_CACHE_SIZE, ttl=_DEFAULT_TTL)
        # НОВОЕ: Глобальный per-user rate limit (0.3с между ЛЮБЫМИ действиями)
        self._global_throttle = TTLCache(maxsize=_MAX_CACHE_SIZE, ttl=_GLOBAL_THROTTLE_TTL)

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if not user_id:
            return await handler(event, data)

        # ═══════════════════════════════════════════════════════════
        # НОВОЕ: ГЛОБАЛЬНЫЙ PER-USER RATE LIMIT (0.3с)
        # ═══════════════════════════════════════════════════════════
        # Блокирует ВСЕ callback_query в течение 0.3с после любого действия.
        # Это предотвращает «2 кнопки одновременно» — основная причина рассинхрона.
        # 0.3с незаметно для человека, но блокирует ботов и double-click.
        if isinstance(event, CallbackQuery):
            global_key = f"global:{user_id}"
            if global_key in self._global_throttle:
                try:
                    await event.answer(texts.ERROR_TOO_FREQUENT, show_alert=False)
                except Exception:
                    pass
                return
            self._global_throttle[global_key] = True

        # ═══════════════════════════════════════════════════════════
        # ACTION-TYPE THROTTLING (2.0с для повторных нажатий)
        # ═══════════════════════════════════════════════════════════
        # 🔥 ИСПРАВЛЕНО: Умный ключ — троттлим по user_id + action_type
        # Это предотвращает создание миллионов уникальных ключей
        if isinstance(event, CallbackQuery):
            # Для callback берем префикс до первого ":"
            # Например: "admin_user_card:123" -> "cb:admin_user_card"
            action_data = event.data or ""
            action_type = action_data.split(":")[0] if ":" in action_data else action_data
            action_key = f"cb:{action_type}"
        elif isinstance(event, Message) and event.text:
            # 🔥 ИСПРАВЛЕНО #15: /start теперь троттится
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
            elif isinstance(event, Message):
                # 🔥 ИСПРАВЛЕНО #15: Для /start показываем alert через answer
                # (Message не имеет .answer(), но можно просто проигнорировать)
                logger.debug(f"Throttled message from user {user_id}: {action_key}")
            return

        # TTLCache сам удалит просроченные записи при следующем обращении
        # Ручная очистка НЕ нужна — это синхронная операция, блокирующая event loop
        self._last_call[key] = True
        return await handler(event, data)