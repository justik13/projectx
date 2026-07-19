import asyncio
import html
import logging
from typing import Optional, List
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache
from bot.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)

_hub_cache = TTLCache(maxsize=HUB_CACHE_MAX_SIZE, ttl=HUB_CACHE_TTL)
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0


async def _safe_delete_batch(bot, chat_id: int, msg_ids: List[int]):
    """Безопасное удаление списка сообщений"""
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramBadRequest as e:
            err_str = str(e).lower()
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


def get_cached_hub_ids(chat_id: int) -> List[int]:
    """Получить текущие ID сообщений хаба из кэша (без модификации кэша)"""
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        return list(cached["ids"])
    return []


async def delete_hub_ids(bot, chat_id: int, msg_ids: List[int]):
    """Удалить конкретные ID сообщений и убрать их из кэша"""
    if not msg_ids:
        return
    await _safe_delete_batch(bot, chat_id, msg_ids)

    lock = get_user_action_lock(chat_id)
    async with lock:
        cached = _hub_cache.get(chat_id)
        if cached and "ids" in cached:
            old_set = set(msg_ids)
            cached["ids"] = [mid for mid in cached["ids"] if mid not in old_set]


async def clear_and_delete_hub(bot, chat_id: int):
    """Принудительная очистка хаба (удалить все сообщения из кэша и очистить кэш)"""
    _maybe_cleanup_cache()
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        await _safe_delete_batch(bot, chat_id, cached["ids"])
    _hub_cache.pop(chat_id, None)


async def render_hub(bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup, parse_mode: str = "HTML") -> int:
    """
    Отрисовка текстового хаба.

    Логика (send-first):
    1. Отправляем новое сообщение
    2. Удаляем старое (если есть в кэше)
    3. Обновляем кэш

    Устраняет пустой экран на 2-3 секунды.
    Кратковременно в чате может быть 2 сообщения (~300мс), но пустого экрана НЕТ.
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
        cached = _hub_cache.get(chat_id)
        old_ids = cached["ids"] if cached and "ids" in cached else []

        # 1. Отправляем новое сообщение
        msg = await bot.send_message(
            chat_id=chat_id, text=text,
            reply_markup=reply_markup, parse_mode=parse_mode
        )

        # 2. Удаляем старые сообщения (если есть)
        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)

        # 3. Обновляем кэш
        _hub_cache[chat_id] = {"ids": [msg.message_id]}

        return msg.message_id


async def send_hub_photo(bot, chat_id: int, photo: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    Отправка фото в хаб (send-first).
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
        cached = _hub_cache.get(chat_id)
        old_ids = cached["ids"] if cached and "ids" in cached else []

        msg = await bot.send_photo(
            chat_id=chat_id, photo=photo, caption=caption,
            reply_markup=reply_markup, parse_mode=parse_mode
        )

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)

        _hub_cache[chat_id] = {"ids": [msg.message_id]}

        return msg.message_id


async def send_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    Отправка документа в хаб (send-first).

    Исключение SMH: файлы — допустимое кратковременное нарушение (по правилам).
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
        cached = _hub_cache.get(chat_id)
        old_ids = cached["ids"] if cached and "ids" in cached else []

        msg = await bot.send_document(
            chat_id=chat_id, document=document, caption=caption,
            reply_markup=reply_markup, parse_mode=parse_mode
        )

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)

        _hub_cache[chat_id] = {"ids": [msg.message_id]}

        return msg.message_id


async def send_hub_invoice(bot, chat_id: int, reply_markup: Optional[InlineKeyboardMarkup] = None, **kwargs) -> int:
    """
    Отправка инвойса Stars (send-first).
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
        cached = _hub_cache.get(chat_id)
        old_ids = cached["ids"] if cached and "ids" in cached else []

        if reply_markup:
            kwargs["reply_markup"] = reply_markup
        msg = await bot.send_invoice(chat_id=chat_id, **kwargs)

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)

        _hub_cache[chat_id] = {"ids": [msg.message_id]}

        return msg.message_id


async def append_hub_document(bot, chat_id: int, document: InputFile, caption: str, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "HTML") -> int:
    """
    Добавление документа к существующему хабу (для пакетной отправки файлов).

    НЕ удаляет старое сообщение, а добавляет к списку ID в кэше.
    Используется в download_conf для отправки .vpn + .conf + инструкция.
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
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
    Добавление текстового сообщения к существующему хабу.

    НЕ удаляет старое сообщение, а добавляет к списку ID в кэше.
    """
    _maybe_cleanup_cache()

    lock = get_user_action_lock(chat_id)
    async with lock:
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
    """Очистка кэша без удаления сообщений (для edge cases)"""
    _hub_cache.pop(chat_id, None)


async def safe_edit_text(message, text: str, **kwargs) -> bool:
    """Безопасное редактирование текста с обработкой 'message is not modified'"""
    try:
        await message.edit_text(text=text, **kwargs)
        return True
    except TelegramBadRequest as e:
        err_str = str(e).lower()
        if "message is not modified" in err_str:
            logger.debug(f"safe_edit_text: message is not modified (user clicked same button)")
        else:
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