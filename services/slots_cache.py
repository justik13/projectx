import asyncio
import logging
from cachetools import TTLCache
from services.amnezia_client import AmneziaClient
from database.models import Server

logger = logging.getLogger(__name__)

# Кэш реального количества пиров на серверах.
# TTL 5 минут (300 секунд), maxsize 100 (с запасом на кол-во серверов)
_slots_cache = TTLCache(maxsize=100, ttl=300)
_locks: dict[int, asyncio.Lock] = {}

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
    # 🔥 ИСПРАВЛЕНО: Если force_refresh=True — пропускаем проверку кэша
    if not force_refresh and server.id in _slots_cache:
        return _slots_cache[server.id]
    
    if server.id not in _locks:
        _locks[server.id] = asyncio.Lock()
    
    async with _locks[server.id]:
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