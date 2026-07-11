import uuid
import re
import logging
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from services.amnezia_client import AmneziaClient
from services.subscription import SubscriptionService
from database.repositories.profiles_repo import (
    create_profile, get_user_profiles_count, get_user_profiles, update_profile
)
from database.repositories.servers_repo import get_server_by_id
from database.models import User, VPNProfile, Server
from bot.constants import AMNEZIA_PROTOCOL

logger = logging.getLogger(__name__)


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession, user: User, server_id: int, device_name: str
    ) -> VPNProfile | None:
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            return None

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

    @staticmethod
    async def enforce_device_limit(
        session: AsyncSession, user: User, new_limit: int
    ) -> int:
        """
        Приостанавливает лишние устройства на Amnezia API при даунгрейде тарифа.
        Возвращает количество приостановленных устройств.
        """
        profiles = await get_user_profiles(session, user.id)
        active_profiles = [p for p in profiles if p.is_active]

        if len(active_profiles) <= new_limit:
            return 0

        # Сортируем по created_at (самые старые — первые на приостановку)
        active_profiles.sort(key=lambda p: p.created_at)
        profiles_to_disable = active_profiles[new_limit:]

        if not profiles_to_disable:
            return 0

        # Группируем по серверам для пакетной обработки
        server_ids = {p.server_id for p in profiles_to_disable}
        stmt = select(Server).where(Server.id.in_(server_ids))
        result = await session.execute(stmt)
        servers_map = {s.id: s for s in result.scalars().all()}

        disabled_count = 0
        profile_ids_to_update = []

        sem = asyncio.Semaphore(10)

        async def _disable_peer(profile: VPNProfile) -> bool:
            async with sem:
                server = servers_map.get(profile.server_id)
                if not server or not server.is_active:
                    return False
                client = AmneziaClient(server.api_url, server.api_key)
                return await client.update_client(
                    client_id=profile.peer_id, status="disabled"
                )

        tasks = [_disable_peer(p) for p in profiles_to_disable]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for profile, result in zip(profiles_to_disable, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Failed to disable peer {profile.id} on API: {result}"
                )
                continue
            if result:
                profile_ids_to_update.append(profile.id)
                disabled_count += 1

        # Обновляем локальную БД
        if profile_ids_to_update:
            from sqlalchemy import update
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids_to_update))
                .values(is_active=False)
            )
            await session.commit()

        logger.info(
            f"Enforce device limit: disabled {disabled_count} devices "
            f"for user {user.telegram_id} (new limit: {new_limit})"
        )
        return disabled_count

    @staticmethod
    async def restore_devices_up_to_limit(
        session: AsyncSession, user: User, new_limit: int
    ) -> int:
        """
        Восстанавливает (reactivate) приостановленные устройства при апгрейде.
        Возвращает количество восстановленных устройств.
        """
        profiles = await get_user_profiles(session, user.id)
        active_count = sum(1 for p in profiles if p.is_active)
        inactive_profiles = [p for p in profiles if not p.is_active]

        if not inactive_profiles or active_count >= new_limit:
            return 0

        slots_available = new_limit - active_count
        profiles_to_enable = inactive_profiles[:slots_available]

        if not profiles_to_enable:
            return 0

        server_ids = {p.server_id for p in profiles_to_enable}
        stmt = select(Server).where(Server.id.in_(server_ids))
        result = await session.execute(stmt)
        servers_map = {s.id: s for s in result.scalars().all()}

        enabled_count = 0
        profile_ids_to_update = []

        sem = asyncio.Semaphore(10)

        async def _enable_peer(profile: VPNProfile) -> bool:
            async with sem:
                server = servers_map.get(profile.server_id)
                if not server or not server.is_active:
                    return False
                client = AmneziaClient(server.api_url, server.api_key)
                return await client.update_client(
                    client_id=profile.peer_id, status="active"
                )

        tasks = [_enable_peer(p) for p in profiles_to_enable]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for profile, result in zip(profiles_to_enable, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to enable peer {profile.id}: {result}")
                continue
            if result:
                profile_ids_to_update.append(profile.id)
                enabled_count += 1

        if profile_ids_to_update:
            from sqlalchemy import update
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids_to_update))
                .values(is_active=True)
            )
            await session.commit()

        logger.info(
            f"Restore devices: enabled {enabled_count} devices "
            f"for user {user.telegram_id} (new limit: {new_limit})"
        )
        return enabled_count