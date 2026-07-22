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


async def _safe_delete_batch(
    bot,
    chat_id: int,
    msg_ids: List[int],
) -> tuple[list[int], list[int]]:
    deleted_ids: list[int] = []
    failed_ids: list[int] = []

    for msg_id in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
            deleted_ids.append(msg_id)
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if (
                "message to delete not found" in err_str
                or "message identifier is not valid" in err_str
                or "chat not found" in err_str
                or "message can't be deleted" in err_str
            ):
                deleted_ids.append(msg_id)
            else:
                failed_ids.append(msg_id)
                logger.warning(
                    "TelegramBadRequest on delete_message %s in %s: %s",
                    msg_id,
                    chat_id,
                    e,
                )
        except Exception as e:
            failed_ids.append(msg_id)
            logger.error(
                "Unexpected error deleting message %s in %s: %s",
                msg_id,
                chat_id,
                e,
            )

    return deleted_ids, failed_ids


def safe(value: Optional[str]) -> str:
    if value is None:
        return "—"
    return html.escape(str(value))


def _maybe_cleanup_cache() -> None:
    global _last_cleanup_time

    now = time.monotonic()
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
    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        return list(cached["ids"])

    try:
        async with session_scope() as session:
            ids = await hub_repo.get_hub_message_ids(session, chat_id)
            _hub_cache[chat_id] = {"ids": list(ids)}
            return list(ids)
    except Exception as e:
        logger.warning("Failed to load hub ids from DB for chat %s: %s", chat_id, e)
        _hub_cache[chat_id] = {"ids": []}
        return []


async def _store_hub_id_in_db(chat_id: int, message_id: int) -> None:
    try:
        async with session_scope() as session:
            await hub_repo.add_hub_message_id(session, chat_id, message_id)
    except Exception as e:
        logger.warning("Failed to store hub id in DB for chat %s: %s", chat_id, e)

    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        if message_id not in cached["ids"]:
            cached["ids"].append(message_id)
    else:
        _hub_cache[chat_id] = {"ids": [message_id]}


async def _remove_hub_ids_from_db(chat_id: int, message_ids: List[int]) -> None:
    if not message_ids:
        return

    try:
        async with session_scope() as session:
            await hub_repo.remove_hub_message_ids(session, chat_id, message_ids)
    except Exception as e:
        logger.warning("Failed to remove hub ids from DB for chat %s: %s", chat_id, e)

    cached = _hub_cache.get(chat_id)
    if cached and "ids" in cached:
        old_set = set(message_ids)
        cached["ids"] = [mid for mid in cached["ids"] if mid not in old_set]


async def get_hub_ids(chat_id: int) -> List[int]:
    return await _load_hub_ids_from_db(chat_id)


async def _delete_hub_messages(bot, chat_id: int, msg_ids: List[int]) -> List[int]:
    if not msg_ids:
        return []

    deleted_ids, failed_ids = await _safe_delete_batch(bot, chat_id, msg_ids)

    if deleted_ids:
        await _remove_hub_ids_from_db(chat_id, deleted_ids)

    if failed_ids:
        logger.warning(
            "Failed to delete %s hub messages in chat %s. "
            "They will be retried on next hub render.",
            len(failed_ids),
            chat_id,
        )

    return failed_ids


async def delete_hub_ids(bot, chat_id: int, msg_ids: List[int]) -> List[int]:
    if not msg_ids:
        return []

    lock = _get_hub_render_lock(chat_id)
    async with lock:
        return await _delete_hub_messages(bot, chat_id, msg_ids)


async def render_hub(
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str = "HTML",
) -> int:
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
            await _delete_hub_messages(bot, chat_id, old_ids)

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
            await _delete_hub_messages(bot, chat_id, old_ids)

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
            await _delete_hub_messages(bot, chat_id, old_ids)

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
            await _delete_hub_messages(bot, chat_id, old_ids)

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