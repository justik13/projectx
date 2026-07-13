import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

class CleanChatMiddleware(BaseMiddleware):
    """
    Удаляет сообщения пользователя для поддержания чистоты чата (SMH).
    
    🔥 ИСПРАВЛЕНО: Не удаляет системные сообщения Telegram:
    - successful_payment (нужно для обработки оплаты)
    - service messages (pin, group creation и т.д.)
    """
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            # 🔥 ИСПРАВЛЕНО: Пропускаем системные сообщения
            if event.successful_payment:
                return await handler(event, data)
            
            # Пропускаем service messages
            if any([
                event.pinned_message,
                event.new_chat_members,
                event.left_chat_member,
                event.new_chat_title,
                event.new_chat_photo,
                event.delete_chat_photo,
                event.group_chat_created,
                event.supergroup_chat_created,
                event.channel_chat_created,
                event.migrate_to_chat_id,
                event.migrate_from_chat_id,
            ]):
                return await handler(event, data)
            
            # Удаляем обычные сообщения пользователя в фоне
            async def _delete_msg():
                try:
                    await event.delete()
                except Exception:
                    pass
            
            asyncio.create_task(_delete_msg())
        
        return await handler(event, data)