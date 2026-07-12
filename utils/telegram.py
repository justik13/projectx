import html
from typing import Optional
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from cachetools import TTLCache

# Кэш ID главного сообщения хаба для каждого пользователя (живет 7 дней)
_hub_cache = TTLCache(maxsize=50000, ttl=86400 * 7)

def safe(value: Optional[str]) -> str:
    """Безопасное экранирование для HTML"""
    if value is None:
        return "—"
    return html.escape(str(value))

async def render_hub(bot, chat_id: int, text: str, reply_markup, parse_mode="HTML") -> int:
    """
    Единая точка рендеринга текстового Хаба.
    Гарантирует, что в чате всегда только ОДНО текстовое сообщение бота.
    """
    msg_id = _hub_cache.get(chat_id)
    if msg_id:
        try:
            await bot.edit_message_text(text, chat_id, msg_id, reply_markup=reply_markup, parse_mode=parse_mode)
            return msg_id
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return msg_id
            # Если сообщение не текстовое (документ, инвойс) или было удалено
            try: 
                await bot.delete_message(chat_id, msg_id)
            except Exception: 
                pass
            
    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)
    _hub_cache[chat_id] = msg.message_id
    return msg.message_id

async def send_hub_document(bot, chat_id: int, document, caption: str, reply_markup, parse_mode="HTML") -> int:
    """Отправляет документ, удаляя предыдущее текстовое сообщение хаба."""
    msg_id = _hub_cache.get(chat_id)
    if msg_id:
        try: 
            await bot.delete_message(chat_id, msg_id)
        except Exception: 
            pass
            
    msg = await bot.send_document(chat_id, document, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
    _hub_cache[chat_id] = msg.message_id
    return msg.message_id

async def send_hub_invoice(bot, chat_id: int, **kwargs) -> int:
    """Отправляет инвойс, удаляя предыдущее текстовое сообщение хаба."""
    msg_id = _hub_cache.get(chat_id)
    if msg_id:
        try: 
            await bot.delete_message(chat_id, msg_id)
        except Exception: 
            pass
            
    msg = await bot.send_invoice(chat_id, **kwargs)
    _hub_cache[chat_id] = msg.message_id
    return msg.message_id

async def safe_edit_text(message, text: str, **kwargs) -> bool:
    try:
        await message.edit_text(text, **kwargs)
        return True
    except Exception:
        return False