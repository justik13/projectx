import asyncio
import html
import logging
from typing import Optional, List
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache
from bot.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО:
# - maxsize уменьшен с 50000 до 10000 (достаточно для 1000 пользователей * 10 чатов)
# - TTL уменьшен с 86400 (24ч) до 43200 (12ч) для более быстрой очистки
# - Добавлена периодическая очистка при достижении 80% лимита
_hub_cache = TTLCache(maxsize=HUB_CACHE_MAX_SIZE, ttl=HUB_CACHE_TTL)
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0  # Очищать раз в час


async def _safe_delete_batch(bot, chat_id: int, msg_ids: List[int]):
    """Безопасное удаление списка сообщений"""
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass


def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _maybe_cleanup_cache() -> None:
    """
    🔥 НОВОЕ: Периодическая очистка кэша при достижении 80% лимита.
    Предотвращает утечку памяти при длительной работе бота.
    """
    global _last_cleanup_time
    now = asyncio.get_event_loop().time()
    if now - _last_cleanup_time < _CLEANUP_INTERVAL:
        return
    
    _last_cleanup_time = now
    
    if len(_hub_cache) >= HUB_CACHE_MAX_SIZE * 0.8:
        # TTLCache автоматически удаляет expired записи при доступе
        # Но мы можем форсировать очистку, обратившись к каждому ключу
        expired_keys = []
        for key in list(_hub_cache.keys()):
            try:
                _ = _hub_cache[key]  # Это триггерит cleanup expired
            except KeyError:
                expired_keys.append(key)
        
        for key in expired_keys:
            try:
                del _hub_cache[key]
            except KeyError:
                pass
        
        logger.info(f"Hub cache cleanup: {len(expired_keys)} expired entries removed")


async def clear_and_delete_hub(bot, chat_id: int):
    """Удаляет все сообщения из кэша хаба и очищает кэш"""
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    _hub_cache.pop(chat_id, None)


async def render_hub(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    """
    Очищает весь текущий хаб (все сообщения в кэше) и отправляет новое текстовое сообщение.
    🔥 ИСПРАВЛЕНО #12: Ждём удаления старых сообщений перед отправкой новых (UX improvement)
    """
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        # 🔥 ИСПРАВЛЕНО #12: Ждём удаления вместо fire-and-forget
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    
    msg = await bot.send_message(
        chat_id=chat_id, text=text,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id


async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """Отправляет фото, удаляя предыдущий хаб
    🔥 ИСПРАВЛЕНО #12: Ждём удаления старых сообщений перед отправкой новых
    """
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        # 🔥 ИСПРАВЛЕНО #12: Ждём удаления вместо fire-and-forget
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    
    msg = await bot.send_photo(
        chat_id=chat_id, photo=photo, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id


async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """Отправляет документ, удаляя предыдущий хаб
    🔥 ИСПРАВЛЕНО #12: Ждём удаления старых сообщений перед отправкой новых
    """
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        # 🔥 ИСПРАВЛЕНО #12: Ждём удаления вместо fire-and-forget
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    
    msg = await bot.send_document(
        chat_id=chat_id, document=document, caption=caption,
        reply_markup=reply_markup, parse_mode=parse_mode
    )
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id


async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    """Отправляет инвойс, удаляя предыдущий хаб
    🔥 ИСПРАВЛЕНО #12: Ждём удаления старых сообщений перед отправкой новых
    """
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        # 🔥 ИСПРАВЛЕНО #12: Ждём удаления вместо fire-and-forget
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    
    msg = await bot.send_invoice(chat_id=chat_id, **kwargs)
    _hub_cache[chat_id] = {"ids": [msg.message_id]}
    return msg.message_id


async def append_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    🔥 ИСКЛЮЧЕНИЕ ИЗ SMH: Отправляет документ и ДОБАВЛЯЕТ его в текущий хаб.
    
    Используется для отправки нескольких файлов подряд (например, .vpn и .conf).
    Это нарушение SMH, но Telegram API не позволяет прикрепить текст к нескольким документам.
    
    Args:
        bot: Экземпляр бота
        chat_id: ID чата
        document: Файл для отправки
        caption: Подпись к файлу
        reply_markup: Inline клавиатура
        parse_mode: Режим парсинга (HTML)
    
    Returns:
        int: message_id отправленного документа
    """
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
    """
    🔥 ИСКЛЮЧЕНИЕ ИЗ SMH: Отправляет текстовое сообщение и ДОБАВЛЯЕТ его в текущий хаб.
    
    Используется после отправки нескольких файлов для добавления инструкции.
    Это нарушение SMH, но необходимо для UX.
    
    Args:
        bot: Экземпляр бота
        chat_id: ID чата
        text: Текст сообщения
        reply_markup: Inline клавиатура
        parse_mode: Режим парсинга (HTML)
    
    Returns:
        int: message_id отправленного сообщения
    """
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