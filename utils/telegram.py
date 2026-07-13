import asyncio
import html
import logging
from typing import Optional, List
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: TTL снижен с 7 дней до 24 часов (86400 сек)
# Структура: {chat_id: {"ids": [msg_id1, msg_id2, ...]}}
_hub_cache = TTLCache(maxsize=50000, ttl=86400)

async def _safe_delete_batch(bot, chat_id: int, msg_ids: List[int]):
    """Безопасное удаление списка сообщений в фоне"""
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass

def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))

async def clear_and_delete_hub(bot, chat_id: int):
    """Удаляет все сообщения из кэша хаба и очищает кэш"""
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    _hub_cache.pop(chat_id, None)

async def render_hub(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    """
    Очищает весь текущий хаб (все сообщения в кэше) и отправляет новое текстовое сообщение.
    """
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        asyncio.create_task(_safe_delete_batch(bot, chat_id, cached["ids"]))
    
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """Отправляет фото, удаляя предыдущий хаб"""
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        asyncio.create_task(_safe_delete_batch(bot, chat_id, cached["ids"]))
    
    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """Отправляет документ, удаляя предыдущий хаб"""
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        asyncio.create_task(_safe_delete_batch(bot, chat_id, cached["ids"]))
    
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    """Отправляет инвойс, удаляя предыдущий хаб"""
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        asyncio.create_task(_safe_delete_batch(bot, chat_id, cached["ids"]))
    
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def append_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    🔥 НОВАЯ ФУНКЦИЯ: Отправляет документ и ДОБАВЛЯЕТ его в текущий хаб.
    Используется для отправки нескольких файлов подряд.
    """
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        cached["ids"].append(msg.message_id)
    else:
        _hub_cache[chat_id] = {"ids": [msg.message_id]}
    
    return msg.message_id

async def append_hub_message(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    🔥 НОВАЯ ФУНКЦИЯ: Отправляет текстовое сообщение и ДОБАВЛЯЕТ его в текущий хаб.
    """
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        cached["ids"].append(msg.message_id)
    else:
        _hub_cache[chat_id] = {"ids": [msg.message_id]}
    
    return msg.message_id

def clear_hub_cache(chat_id: int) -> None:
    """Очищает кэш хаба для конкретного чата (без удаления сообщений)"""
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