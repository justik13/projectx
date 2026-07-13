import asyncio
import html
import logging
from typing import Optional, Dict, Any
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# 🔥 Кэш теперь хранит не только ID, но и ТИП сообщения (text, document, photo, invoice)
_hub_cache = TTLCache(maxsize=50000, ttl=86400 * 7)

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
        msg_type = cached.get("type", "text")
        
        if msg_type == "text":
            try:
                await bot.edit_message_text(
                    text=text, chat_id=chat_id, message_id=msg_id,
                    reply_markup=reply_markup, parse_mode=parse_mode
                )
                return msg_id
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    return msg_id
                # Сообщение не текстовое или было удалено
        
        # 🔥 КРИТИЧНО: Если тип не text (document, photo, invoice) или edit упал
        # Удаляем старое сообщение В ФОНЕ, чтобы не блокировать интерфейс!
        # Пользователь мгновенно получит новый текст, а старый документ исчезнет через 0.5 сек.
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

async def send_hub_invoice(bot, chat_id: int, **kwargs) -> int:
    cached = _hub_cache.get(chat_id)
    if cached:
        asyncio.create_task(_safe_delete(bot, chat_id, cached["id"]))
        
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = {"id": msg.message_id, "type": "invoice"}
    return msg.message_id

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