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
- Добавлен глобальный per-user rate limit (TTLCache 0.3с)
- Уменьшен maxsize с 10000 до 5000 (достаточно для 1000 пользователей)
- Добавлен явный cleanup при достижении лимита
- Ключ включает только action_type, а не полный callback_data для экономии памяти

🔥 ИСПРАВЛЕНО #15: /start throttle
- /start теперь троттится на уровне action-type (2.0с между повторными запусками)
- Раньше: /start — это Message, global throttle (0.3с) работал, но action-type не применялся
- Теперь: /start добавлен в список троттлимых команд, защита от спама
"""
import asyncio
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
            logger.debug("Throttling cleanup: removed %d expired entries", len(expired_keys))