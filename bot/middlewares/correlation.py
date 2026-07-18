import logging
import uuid
from contextvars import ContextVar
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)
request_id_var: ContextVar[str] = ContextVar('request_id', default='system')


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get('system')
        return True


def get_current_request_id() -> str:
    return request_id_var.get('system')


def set_request_id(request_id: str) -> None:
    request_id_var.set(request_id)


class CorrelationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        request_id = uuid.uuid4().hex[:8]
        request_id_var.set(request_id)
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
            logger.error(
                "[%s] Unhandled exception in %s: %s: %s",
                request_id,
                event_type,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            raise