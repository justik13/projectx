import asyncio
import html
import logging
from typing import Optional, List
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: Кэш теперь хранит СПИСОК ID сообщений (а не один ID)
# Это позволяет удалять несколько сообщений (например, .vpn + .conf) при следующем действии
_hub_cache = TTLCache(maxsize=50000, ttl=86400)  # chat_id -> List[message_id]

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
    """
    Отправляет текстовый хаб и удаляет ВСЕ предыдущие сообщения из кэша.
    """
    cached = _hub_cache.get(chat_id)
    if cached:
        # 🔥 Удаляем ВСЕ сообщения из кэша
        for msg_id in cached:
            asyncio.create_task(_safe_delete(bot, chat_id, msg_id))
    
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    # 🔥 Сохраняем только ID нового текстового хаба
    _hub_cache[chat_id] = [msg.message_id]
    return msg.message_id

async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    cached = _hub_cache.get(chat_id)
    if cached:
        for msg_id in cached:
            asyncio.create_task(_safe_delete(bot, chat_id, msg_id))
    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = [msg.message_id]
    return msg.message_id

async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    """
    Отправляет документ и удаляет ВСЕ предыдущие сообщения из кэша.
    """
    cached = _hub_cache.get(chat_id)
    if cached:
        for msg_id in cached:
            asyncio.create_task(_safe_delete(bot, chat_id, msg_id))
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = [msg.message_id]
    return msg.message_id

async def send_hub_document_persistent(bot, chat_id: int, document: InputFile, caption: str, parse_mode: str = "HTML") -> int:
    """
    🔥 НОВАЯ ФУНКЦИЯ: Отправляет документ и ДОБАВЛЯЕТ его ID в кэш (не удаляя предыдущие).
    Используется для отправки нескольких файлов подряд (.vpn + .conf).
    Все файлы будут удалены при следующем вызове render_hub.
    """
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        parse_mode=parse_mode
    )
    # 🔥 Добавляем ID в список (не заменяем)
    cached = _hub_cache.get(chat_id, [])
    cached.append(msg.message_id)
    _hub_cache[chat_id] = cached
    return msg.message_id

async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    """
    Отправляет инвойс и удаляет ВСЕ предыдущие сообщения из кэша.
    """
    cached = _hub_cache.get(chat_id)
    if cached:
        for msg_id in cached:
            asyncio.create_task(_safe_delete(bot, chat_id, msg_id))
    
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = [msg.message_id]
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