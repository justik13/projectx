import asyncio
import html
import logging
from typing import Optional, Dict, Any
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: TTL снижен с 7 дней до 24 часов (86400 сек)
_hub_cache = TTLCache(maxsize=50000, ttl=86400)

async def _safe_delete(bot, chat_id: int, msg_id: int):
    """Безопасное удаление сообщения в фоне (не блокирует UI)"""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))

async def render_hub(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    cached = _hub_cache.get(chat_id)
    if cached:
        msg_id = cached["id"]
        asyncio.create_task(_safe_delete(bot, chat_id, msg_id))
        
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"id": msg.message_id, "type": "text"}
    return msg.message_id

async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    cached = _hub_cache.get(chat_id)
    if cached:
        asyncio.create_task(_safe_delete(bot, chat_id, cached["id"]))
    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"id": msg.message_id, "type": "photo"}
    return msg.message_id

async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    cached = _hub_cache.get(chat_id)
    if cached:
        asyncio.create_task(_safe_delete(bot, chat_id, cached["id"]))
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"id": msg.message_id, "type": "document"}
    return msg.message_id

async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    """
    🔥 ИСПРАВЛЕНО: reply_markup теперь принимается для кнопок внутри инвойса!
    """
    cached = _hub_cache.get(chat_id)
    if cached:
        asyncio.create_task(_safe_delete(bot, chat_id, cached["id"]))
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = {"id": msg.message_id, "type": "invoice"}
    return msg.message_id

def clear_hub_cache(chat_id: int) -> None:
    """Очищает кэш хаба для конкретного чата (вызывать при ручном удалении)"""
    _hub_cache.pop(chat_id, None)

async def safe_edit_text(message, text: str, **kwargs) -> bool:
    try:
        await message.edit_text(text=text, **kwargs)
        return True
    except Exception:
        return False

async def safe_delete_message(message) -> bool:
    try:
        await message.delete()
        return True
    except Exception:
        return False

async def safe_answer(callback, text: Optional[str] = None, show_alert: bool = False) -> bool:
    try:
        await callback.answer(text, show_alert=show_alert)
        return True
    except Exception:
        return False