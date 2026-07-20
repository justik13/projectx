import asyncio
import html
import logging
import time
from typing import Optional, List

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, InputFile
from cachetools import TTLCache

from bot.constants import HUB_CACHE_MAX_SIZE, HUB_CACHE_TTL
from database.connection import session_scope
from database.repositories import hub_repo

logger = logging.getLogger(__name__)

# In-memory cache оставлен как быстрый fallback и как защита от лишних
# запросов к БД внутри одного процесса.
#
# Основной источник истины для hub message id теперь PostgreSQL.
# Это нужно, чтобы после рестарта бота старые хабы всё равно удалялись.
_hub_cache = TTLCache(maxsize=HUB_CACHE_MAX_SIZE, ttl=HUB_CACHE_TTL)

_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0

_hub_render_locks: dict[int, tuple[asyncio.Lock, float]] = {}
_RENDER_LOCK_TTL = 3600.0
_last_render_lock_cleanup: float = 0.0


def _get_hub_render_lock(chat_id: int) -> asyncio.Lock:
    global _last_render_lock_cleanup

    now = time.monotonic()

    if now - _last_render_lock_cleanup > _CLEANUP_INTERVAL:
        _cleanup_render_locks(now)
        _last_render_lock_cleanup = now

    if chat_id not in _hub_render_locks:
        _hub_render_locks[chat_id] = (asyncio.Lock(), now)
    else:
        lock, _ = _hub_render_locks[chat_id]
        _hub_render_locks[chat_id] = (lock, now)

    return _hub_render_locks[chat_id][0]


def _cleanup_render_locks(now: float) -> None:
    old = [
        cid
        for cid, (lock, last_used) in _hub_render_locks.items()
        if now - last_used > _RENDER_LOCK_TTL and not lock.locked()
    ]

    for cid in old:
        del _hub_render_locks[cid]

    if old:
        logger.debug(
            "Hub render locks cleanup: removed %s, %s remaining",
            len(old),
            len(_hub_render_locks),
        )


async def _safe_delete_batch(bot, chat_id: int, msg_ids: List[int]):
    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except TelegramBadRequest as e:
            err_str = str(e).lower()

            if (
                "message to delete not found" in err_str
                or "message can't be deleted" in err_str
                or "chat not found" in err_str
            ):
                pass
            else:
                logger.warning(
                    "TelegramBadRequest on delete_message %s in %s: %s",
                    msg_id,
                    chat_id,
                    e,
                )
        except Exception as e:
            logger.error(
                "Unexpected error deleting message %s in %s: %s",
                msg_id,
                chat_id,
                e,
            )


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

        logger.info(
            "Hub cache cleanup: %s expired entries removed",
            len(expired_keys),
        )


async def _load_hub_ids_from_db(chat_id: int) -> List[int]:
    """
    Загружает сохранённые hub message id из PostgreSQL.

    Если БД временно недоступна, используем in-memory fallback.
    """
    cached = _hub_cache.get(chat_id)

    if cached and "ids" in cached:
        return list(cached["ids"])

    try:
        async with session_scope() as session:
            ids = await hub_repo.get_hub_message_ids(session, chat_id)

            if ids:
                _hub_cache[chat_id] = {"ids": list(ids)}

            return ids

    except Exception as e:
        logger.warning(
            "Failed to load hub ids from DB for chat %s: %s",
            chat_id,
            e,
        )

        return []


async def _store_hub_id_in_db(chat_id: int, message_id: int) -> None:
    """
    Сохраняет новый hub message id в PostgreSQL.
    """
    try:
        async with session_scope() as session:
            await hub_repo.add_hub_message_id(session, chat_id, message_id)
    except Exception as e:
        logger.warning(
            "Failed to store hub id in DB for chat %s: %s",
            chat_id,
            e,
        )

    cached = _hub_cache.get(chat_id)

    if cached and "ids" in cached:
        if message_id not in cached["ids"]:
            cached["ids"].append(message_id)
    else:
        _hub_cache[chat_id] = {"ids": [message_id]}


async def _remove_hub_ids_from_db(chat_id: int, message_ids: List[int]) -> None:
    """
    Удаляет hub message id из PostgreSQL после удаления сообщений.
    """
    if not message_ids:
        return

    try:
        async with session_scope() as session:
            await hub_repo.remove_hub_message_ids(session, chat_id, message_ids)
    except Exception as e:
        logger.warning(
            "Failed to remove hub ids from DB for chat %s: %s",
            chat_id,
            e,
        )

    cached = _hub_cache.get(chat_id)

    if cached and "ids" in cached:
        old_set = set(message_ids)
        cached["ids"] = [
            mid for mid in cached["ids"] if mid not in old_set
        ]


def get_cached_hub_ids(chat_id: int) -> List[int]:
    """
    Синхронный хелпер для мест, где нужно быстро получить текущие hub id.

    Основной источник — PostgreSQL, но этот метод возвращает in-memory cache.
    Cache заполняется во время render_hub / append_hub_*.
    """
    cached = _hub_cache.get(chat_id)

    if cached and "ids" in cached:
        return list(cached["ids"])

    return []


async def delete_hub_ids(bot, chat_id: int, msg_ids: List[int]):
    if not msg_ids:
        return

    await _safe_delete_batch(bot, chat_id, msg_ids)

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        await _remove_hub_ids_from_db(chat_id, msg_ids)


async def clear_and_delete_hub(bot, chat_id: int):
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        db_ids = await _load_hub_ids_from_db(chat_id)

        cached = _hub_cache.get(chat_id)
        cache_ids = cached["ids"] if cached and "ids" in cached else []

        all_ids = list(dict.fromkeys(db_ids + cache_ids))

        if all_ids:
            await _safe_delete_batch(bot, chat_id, all_ids)
            await _remove_hub_ids_from_db(chat_id, all_ids)

        _hub_cache.pop(chat_id, None)


async def render_hub(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str = "HTML",
) -> int:
    """
    Главный hub-рендер.

    Логика:
    1. Загружаем старые hub id из PostgreSQL.
    2. Отправляем новое сообщение.
    3. Удаляем старые сообщения.
    4. Удаляем старые id из PostgreSQL.
    5. Сохраняем новый id в PostgreSQL.

    Это устраняет дубли после рестарта бота.
    """
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)
            await _remove_hub_ids_from_db(chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def send_hub_photo(
    bot,
    chat_id: int,
    photo: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        msg = await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)
            await _remove_hub_ids_from_db(chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def send_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        msg = await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)
            await _remove_hub_ids_from_db(chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def send_hub_invoice(
    bot,
    chat_id: int,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    **kwargs,
) -> int:
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        old_ids = await _load_hub_ids_from_db(chat_id)

        if reply_markup:
            kwargs["reply_markup"] = reply_markup

        msg = await bot.send_invoice(chat_id=chat_id, **kwargs)

        if old_ids:
            await _safe_delete_batch(bot, chat_id, old_ids)
            await _remove_hub_ids_from_db(chat_id, old_ids)

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def append_hub_document(
    bot,
    chat_id: int,
    document: InputFile,
    caption: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    """
    Добавляет документ к текущему hub-контексту.

    Используется, например, для отправки файлов конфигурации.
    """
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        msg = await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


async def append_hub_message(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    parse_mode: str = "HTML",
) -> int:
    """
    Добавляет текстовое сообщение к текущему hub-контексту.
    """
    _maybe_cleanup_cache()

    lock = _get_hub_render_lock(chat_id)

    async with lock:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

        await _store_hub_id_in_db(chat_id, msg.message_id)

        return msg.message_id


def clear_hub_cache(chat_id: int) -> None:
    _hub_cache.pop(chat_id, None)


async def safe_edit_text(message, text: str, **kwargs) -> bool:
    try:
        await message.edit_text(text=text, **kwargs)
        return True
    except TelegramBadRequest as e:
        err_str = str(e).lower()

        if "message is not modified" in err_str:
            logger.debug(
                "safe_edit_text: message is not modified "
                "(user clicked same button)"
            )
        else:
            logger.warning("safe_edit_text TelegramBadRequest: %s", e)

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


async def safe_answer(
    callback,
    text: Optional[str] = None,
    show_alert: bool = False,
) -> bool:
    try:
        await callback.answer(text, show_alert=show_alert)
        return True
    except Exception:
        return False