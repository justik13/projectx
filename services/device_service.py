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

# Глобальный словарь блокировок по user_id
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession, user: User, server_id: int, device_name: str
    ) -> VPNProfile | None:
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            return None

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
            # ИСПРАВЛЕНО: теперь возвращает DTO AmneziaClientCreateResponse
            result = await client.create_user(client_name=client_name, expires_at=expires_ts)

            if not result:
                return None

            # ИСПРАВЛЕНО: доступ к полям через DTO (типизированно)
            peer_id = result.id
            raw_config = result.config

            try:
                profile = await create_profile(
                    session, user_id=user.id, server_id=server.id,
                    device_name=device_name, peer_id=peer_id,
                    raw_config=raw_config
                )
                return profile
            except Exception as e:
                logger.error(f"Failed to create profile in DB: {e}")
                try:
                    await client.delete_user(client_id=peer_id)
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