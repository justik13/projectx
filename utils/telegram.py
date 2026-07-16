import asyncio
import html
import logging
from typing import Optional, List
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache
from bot.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL

logger = logging.getLogger(__name__)

_hub_cache = TTLCache(maxsize=HUB_CACHE_MAX_SIZE, ttl=HUB_CACHE_TTL)
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0

async def _safe_delete_batch(bot, chat_id: int, msg_ids: List[int]):
    """
    🔥 ИСПРАВЛЕНО (Этап 2): Безопасное удаление с отловом TelegramBadRequest.
    Больше не проглатывает критические ошибки API.
    """
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            # Игнорируем ожидаемые ошибки (сообщение уже удалено или не найдено)
            if "message to delete not found" in err_str or "message can't be deleted" in err_str or "chat not found" in err_str:
                pass
            else:
                logger.warning(f"TelegramBadRequest on delete_message {msg_id} in {chat_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error deleting message {msg_id} in {chat_id}: {e}")

def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))

def _maybe_cleanup_cache() -> None:
    global _last_cleanup_time
    now = asyncio.get_event_loop().time()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL:
        return
    _last_cleanup_time = now
    if len(_hub_cache) >= HUB_CACHE_MAX_SIZE * 0.8:
        expired_keys = []
        for key in list(_hub_cache.keys()):
            try:
                _ = _hub_cache[key]
            except KeyError:
                expired_keys.append(key)
        for key in expired_keys:
            try:
                del _hub_cache[key]
            except KeyError:
                pass
        logger.info(f"Hub cache cleanup: {len(expired_keys)} expired entries removed")

async def clear_and_delete_hub(bot, chat_id: int):
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    _hub_cache.pop(chat_id, None)

async def render_hub(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id

async def append_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    _maybe_cleanup_cache()
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
    _maybe_cleanup_cache()
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
    _hub_cache.pop(chat_id, None)

async def safe_edit_text(message, text: str, **kwargs) -> bool:
    try:
        await message.edit_text(text=text, **kwargs)
        return True
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logger.warning(f"safe_edit_text TelegramBadRequest: {e}")
        return False
    except Exception:
        return False

async def safe_delete_message(message) -> bool:
    try:
        await message.delete()
        return True
    except TelegramBadRequest:
        return False
    except Exception:
        return False

async def safe_answer(callback, text: Optional[str] = None, show_alert: bool = False) -> bool:
    try:
        await callback.answer(text, show_alert=show_alert)
        return True
    except Exception:
        return False