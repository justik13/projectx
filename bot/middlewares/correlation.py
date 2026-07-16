"""
CorrelationMiddleware — генерация request_id для каждого события.

🔥 ИСПРАВЛЕНО #10: Correlation ID для логов.

Проблема:
Все логи писались без request_id / correlation_id. При отладке невозможно
отследить цепочку действий одного пользователя через:
middleware → handler → service → API.

Решение:
1. Генерируем UUID в UserContextMiddleware для каждого события
2. Сохраняем в contextvars (thread-safe, async-safe)
3. Все logger.info() / logger.error() автоматически получают префикс [req_abc123]
4. При ошибке в global_error_handler можно найти ВСЕ логи по этому request_id

Реализация:
- contextvars.ContextVar — единственный thread-safe способ передачи данных
  через async-стек без явной передачи параметра
- logging.Filter — стандартный механизм добавления полей в LogRecord
- Короткий 8-символьный hex для читаемости логов (полный UUID слишком длинный)
"""
import logging
import uuid
from contextvars import ContextVar
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Глобальная переменная для хранения ID запроса.
# contextvars гарантирует, что каждый async-task видит своё значение,
# даже если в event loop выполняются сотни запросов параллельно.
request_id_var: ContextVar[str] = ContextVar('request_id', default='system')


class CorrelationFilter(logging.Filter):
    """
    Добавляет request_id в каждый LogRecord.
    
    Используется в logging.basicConfig() или через dictConfig.
    Формат: %(request_id)s в formatter → [abc12345]
    
    Если request_id не установлен (например, background worker),
    используется значение по умолчанию 'system'.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get('system')
        return True


def get_current_request_id() -> str:
    """Возвращает текущий request_id (для использования в сервисах)."""
    return request_id_var.get('system')


def set_request_id(request_id: str) -> None:
    """Устанавливает request_id (используется middleware и workers)."""
    request_id_var.set(request_id)


class CorrelationMiddleware(BaseMiddleware):
    """
    Генерирует уникальный request_id для каждого события Telegram.
    
    Работает для Message и CallbackQuery.
    request_id сохраняется в contextvars и доступен во всех downstream-вызовах.
    
    Порядок middleware КРИТИЧЕН:
    1. CorrelationMiddleware (ПЕРВЫМ) — генерирует ID
    2. DBSessionMiddleware
    3. CleanChatMiddleware
    4. UserContextMiddleware
    5. ThrottlingMiddleware
    6. ActionLockMiddleware
    7. ChatActionMiddleware
    """
    async def __call__(self, handler, event, data):
        # Генерируем короткий 8-символьный hex из UUID
        # Полный UUID (36 символов) слишком длинный для логов
        request_id = uuid.uuid4().hex[:8]
        request_id_var.set(request_id)
        
        # Определяем тип события для логирования
        if isinstance(event, CallbackQuery):
            event_type = "callback"
            event_data = (event.data or "")[:50]
            user_id = event.from_user.id if event.from_user else 0
        elif isinstance(event, Message):
            event_type = "message"
            event_data = (event.text or "")[:50] if event.text else ""
            user_id = event.from_user.id if event.from_user else 0
        else:
            event_type = "unknown"
            event_data = ""
            user_id = 0
        
        logger.info(
            "[%s] %s from user %d: %s",
            request_id,
            event_type,
            user_id,
            event_data,
        )
        
        try:
            return await handler(event, data)
        except Exception as e:
            # Логируем ошибку с request_id для трассировки
            logger.error(
                "[%s] Unhandled exception in %s: %s: %s",
                request_id,
                event_type,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            raise