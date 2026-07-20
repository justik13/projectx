import logging
import uuid
from contextvars import ContextVar

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

request_id_var: ContextVar[str] = ContextVar("request_id", default="system")


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get("system")
        return True


def get_current_request_id() -> str:
    return request_id_var.get("system")


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


def _redact_callback_data(data: str | None) -> str:
    """
    Логируем только action-prefix callback_data.

    Пример:
    - было: check_payment:123
    - стало: action=check_payment

    Это исключает утечку ID, токенов и других чувствительных параметров
    в journalctl.
    """
    if not data:
        return "action=empty"

    action = data.split(":", 1)[0]
    if not action:
        return "action=empty"

    return f"action={action}"


def _message_log_summary(message: Message) -> str:
    """
    Больше НЕ логируем тело сообщений.

    Вместо этого логируем только безопасные метаданные:
    - тип контента;
    - длину текста, если текст есть;
    - наличие successful_payment.

    Это защищает секреты, которые админ может вводить в FSM-состояниях:
    API-ключи, секреты, токены, пароли и т.д.
    """
    if message.successful_payment:
        return "content_type=successful_payment"

    content_type = message.content_type or "unknown"

    if message.text:
        return f"content_type={content_type}, text_len={len(message.text)}"

    if message.caption:
        return f"content_type={content_type}, caption_len={len(message.caption)}"

    return f"content_type={content_type}"


class CorrelationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        request_id = uuid.uuid4().hex[:8]
        request_id_var.set(request_id)

        if isinstance(event, CallbackQuery):
            event_type = "callback"
            event_data = _redact_callback_data(event.data)
            user_id = event.from_user.id if event.from_user else 0
        elif isinstance(event, Message):
            event_type = "message"
            event_data = _message_log_summary(event)
            user_id = event.from_user.id if event.from_user else 0
        else:
            event_type = type(event).__name__ or "unknown"
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
            # Не логируем текст исключения здесь.
            # Полный stack trace будет обработан глобальным error handler'ом,
            # где вывод должен быть дополнительно санитизирован.
            logger.error(
                "[%s] Unhandled exception in %s: %s",
                request_id,
                event_type,
                type(e).__name__,
            )
            raise