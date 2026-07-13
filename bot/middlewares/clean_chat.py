import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

class CleanChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            async def _delete_msg():
                try:
                    await event.delete()
                except Exception:
                    pass
            
            asyncio.create_task(_delete_msg())
            
        return await handler(event, data)