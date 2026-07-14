import uuid
import re
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from services.amnezia_client import AmneziaClient
from services.subscription import SubscriptionService
from database.repositories.profiles_repo import create_profile, get_user_profiles_count
from database.repositories.servers_repo import get_server_by_id
from database.models import User, VPNProfile
from bot.constants import AMNEZIA_PROTOCOL

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: Используем WeakValueDictionary для автоматической очистки locks
# когда user_id больше не используется. Это предотвращает утечку памяти при 1000+ пользователей.
import weakref

_user_locks: dict[int, asyncio.Lock] = {}
_locks_last_access: dict[int, float] = {}
_LOCKS_CLEANUP_THRESHOLD = 500  # Очищать когда больше 500 locks
_LOCKS_MAX_AGE_SECONDS = 3600  # Удалять locks старше 1 часа


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """
    Получает lock для user_id. Создает новый если не существует.
    🔥 ИСПРАВЛЕНО: Lazy creation внутри async context для совместимости с Python 3.10+
    и автоматическая очистка старых locks для предотвращения утечки памяти.
    """
    now = asyncio.get_event_loop().time()
    
    # Обновляем время последнего доступа
    if user_id in _user_locks:
        _locks_last_access[user_id] = now
        return _user_locks[user_id]
    
    # Периодическая очистка старых locks
    if len(_user_locks) > _LOCKS_CLEANUP_THRESHOLD:
        _cleanup_stale_locks(now)
    
    # Создаем новый lock
    lock = asyncio.Lock()
    _user_locks[user_id] = lock
    _locks_last_access[user_id] = now
    return lock


def _cleanup_stale_locks(now: float) -> None:
    """Удаляет locks, которые не использовались больше _LOCKS_MAX_AGE_SECONDS"""
    stale_ids = [
        uid for uid, last_access in _locks_last_access.items()
        if now - last_access > _LOCKS_MAX_AGE_SECONDS
    ]
    for uid in stale_ids:
        # Удаляем только если lock не заблокирован
        lock = _user_locks.get(uid)
        if lock and not lock.locked():
            del _user_locks[uid]
            del _locks_last_access[uid]
    
    if stale_ids:
        logger.debug(f"Cleaned up {len(stale_ids)} stale user locks")


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession, user: User, server_id: int, device_name: str
    ) -> VPNProfile | None:
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            return None
        
        # 🔥 ИСПРАВЛЕНО: Атомарная проверка и создание под локом
        lock = _get_user_lock(user.id)
        async with lock:
            profiles_count = await get_user_profiles_count(session, user.id)
            if profiles_count >= user.device_limit:
                return None
            
            short_hash = uuid.uuid4().hex[:4]
            clean_device_name = re.sub(r'[^a-zA-Z0-9]', '', device_name)[:10]
            client_name = f"tg_{user.telegram_id}_{clean_device_name}_{short_hash}"
            
            expires_ts = await SubscriptionService.get_expires_timestamp(user)
            
            client = AmneziaClient(server.api_url, server.api_key)
            result = await client.create_user(client_name=client_name, expires_at=expires_ts)
            
            if not result or not result.get("id") or not result.get("config"):
                return None
            
            try:
                profile = await create_profile(
                    session, user_id=user.id, server_id=server.id,
                    device_name=device_name, peer_id=result.get("id"),
                    raw_config=result.get("config")
                )
                return profile
            except Exception as e:
                logger.error(f"Failed to create profile in DB: {e}")
                try:
                    await client.delete_user(client_id=result.get("id"))
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback: {rollback_error}")
                return None
    
    @staticmethod
    async def delete_device(session: AsyncSession, profile: VPNProfile) -> bool:
        from database.repositories.profiles_repo import delete_profile
        from database.repositories.servers_repo import get_server_by_id
        
        server = await get_server_by_id(session, profile.server_id)
        if server:
            client = AmneziaClient(server.api_url, server.api_key)
            deleted = await client.delete_user(client_id=profile.peer_id)
            if not deleted:
                return False
            await delete_profile(session, profile)
            return True
        return False