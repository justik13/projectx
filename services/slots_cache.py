import asyncio
import logging
import time
from cachetools import TTLCache
from services.amnezia_client import AmneziaClient
from database.models import Server

logger = logging.getLogger(__name__)

# Кэш реального количества пиров на серверах.
# TTL 5 минут (300 секунд), maxsize 100 (с запасом на кол-во серверов)
_slots_cache = TTLCache(maxsize=100, ttl=300)

# 🔥 ИСПРАВЛЕНО #4: (lock, last_used_timestamp) вместо просто lock
_locks: dict[int, tuple[asyncio.Lock, float]] = {}

_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0  # Проверять cleanup раз в час
_LOCK_TTL = 3600.0  # Удалять locks не использовавшиеся > 1 часа


async def get_real_peer_count(server: Server, force_refresh: bool = False) -> int:
    """
    Возвращает реальное количество пиров на сервере через API.
    Использует кэш с TTL 5 минут для снижения нагрузки.
    🔥 ИСПРАВЛЕНО: Добавлен параметр force_refresh для критических операций.
    
    Args:
        server: Объект сервера
        force_refresh: Если True — игнорирует кэш и всегда запрашивает API.
                       Используется при создании устройства для точности.
    
    Returns:
        int: Количество пиров.
        -1: Если API недоступен (ошибка сети/таймаут).
    """
    global _last_cleanup_time
    
    now = time.monotonic()
    
    # 🔥 ИСПРАВЛЕНО #4: Периодический cleanup locks
    if now - _last_cleanup_time > _CLEANUP_INTERVAL:
        _cleanup_old_locks(now)
        _last_cleanup_time = now
    
    # 🔥 ИСПРАВЛЕНО: Если force_refresh=True — пропускаем проверку кэша
    if not force_refresh and server.id in _slots_cache:
        return _slots_cache[server.id]
    
    # Получаем или создаём lock для этого сервера
    if server.id not in _locks:
        _locks[server.id] = (asyncio.Lock(), now)
    else:
        lock, _ = _locks[server.id]
        _locks[server.id] = (lock, now)  # Обновляем last_used
    
    lock = _locks[server.id][0]
    
    async with lock:
        # Double-check после получения блокировки (только если не force_refresh)
        if not force_refresh and server.id in _slots_cache:
            return _slots_cache[server.id]
        
        client = AmneziaClient(server.api_url, server.api_key)
        try:
            clients = await client.get_all_clients()
            count = len(clients) if clients is not None else 0
            _slots_cache[server.id] = count
            logger.info(
                f"Cached real peer count for server {server.id} ({server.name}): "
                f"{count}/{server.max_clients}"
            )
            return count
        except Exception as e:
            logger.error(
                f"Failed to get real peer count for server {server.id} "
                f"({server.name}): {e}"
            )
            return -1


def _cleanup_old_locks(now: float):
    """
    Удаляет locks не использовавшиеся > 1 часа и не захваченные в данный момент.
    🔥 ИСПРАВЛЕНО #4: Предотвращает утечку памяти.
    """
    old_servers = [
        sid for sid, (lock, last_used) in _locks.items()
        if now - last_used > _LOCK_TTL and not lock.locked()
    ]
    for sid in old_servers:
        del _locks[sid]
    
    if old_servers:
        logger.debug(
            f"Slots cache locks cleanup: removed {len(old_servers)} old locks, "
            f"{len(_locks)} remaining"
        )