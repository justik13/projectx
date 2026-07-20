import asyncio
import logging
import time

from cachetools import TTLCache

from database.models import Server
from services.amnezia_client import AmneziaClient

logger = logging.getLogger(__name__)

_slots_cache = TTLCache(maxsize=100, ttl=300)
_locks: dict[int, tuple[asyncio.Lock, float]] = {}
_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0
_LOCK_TTL = 3600.0


async def get_real_peer_count(
    server: Server,
    force_refresh: bool = False,
) -> int:
    """
    Возвращает реальное количество пиров на сервере.

    Важно:
    - если API недоступен или вернул ошибку, возвращаем -1;
    - ошибочный ноль больше НЕ кэшируется;
    - кэшируется только успешный ответ API.

    Поведение:
    - 0..N — реальный ответ API;
    - -1 — данные получить не удалось.

    Вышестоящий код должен трактовать -1 как «неизвестно»,
    а не как «пусто».
    """
    global _last_cleanup_time

    now = time.monotonic()

    if now - _last_cleanup_time > _CLEANUP_INTERVAL:
        _cleanup_old_locks(now)
        _last_cleanup_time = now

    if not force_refresh and server.id in _slots_cache:
        return _slots_cache[server.id]

    if server.id not in _locks:
        _locks[server.id] = (asyncio.Lock(), now)
    else:
        lock, _ = _locks[server.id]
        _locks[server.id] = (lock, now)

    lock = _locks[server.id][0]

    async with lock:
        if not force_refresh and server.id in _slots_cache:
            return _slots_cache[server.id]

        client = AmneziaClient(server.api_url, server.api_key)

        try:
            clients = await client.get_all_clients()
        except Exception as e:
            logger.error(
                "Failed to get real peer count for server %s (%s): %s",
                server.id,
                server.name,
                e,
            )
            return -1

        if clients is None:
            logger.warning(
                "API returned no data for server %s (%s). "
                "Peer count is unknown, returning -1.",
                server.id,
                server.name,
            )
            return -1

        count = len(clients)

        _slots_cache[server.id] = count

        logger.info(
            "Cached real peer count for server %s (%s): %s/%s",
            server.id,
            server.name,
            count,
            server.max_clients,
        )

        return count


def _cleanup_old_locks(now: float) -> None:
    old_servers = [
        sid
        for sid, (lock, last_used) in _locks.items()
        if now - last_used > _LOCK_TTL and not lock.locked()
    ]

    for sid in old_servers:
        del _locks[sid]

    if old_servers:
        logger.debug(
            "Slots cache locks cleanup: removed %s old locks, "
            "%s remaining",
            len(old_servers),
            len(_locks),
        )