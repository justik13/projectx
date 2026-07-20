import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)


class PrivateChatMiddleware(BaseMiddleware):
    """
    Разрешает обработку только в личных чатах.

    Если бот оказывается в группе, супергруппе или канале:
    - message-события игнорируются;
    - callback-события игнорируются;
    - удаление сообщений через CleanChat в группах больше не происходит,
      потому что CleanChat будет регистрироваться после этого middleware
      и просто не получит событие в обработку.

    Webhook и внутренние технические обработчики не затрагиваются.
    """

    async def __call__(self, handler, event, data):
        chat = None

        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery):
            if event.message:
                chat = event.message.chat

        # Если чат не определён, пропускаем событие дальше.
        # Это нужно для служебных update'ов, где chat может отсутствовать.
        if chat is None:
            return await handler(event, data)

        if chat.type != "private":
            logger.debug(
                "Ignoring non-private chat event: chat_id=%s, chat_type=%s",
                chat.id,
                chat.type,
            )
            return None

        return await handler(event, data)