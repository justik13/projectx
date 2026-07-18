import uuid
import re
import logging
import asyncio
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from services.amnezia_client import AmneziaClient
from services.subscription import SubscriptionService
from services.audit_service import AuditService
from services.slots_cache import get_real_peer_count
from database.repositories.profiles_repo import create_profile, get_user_profiles_count
from database.repositories.servers_repo import get_server_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.models import User, VPNProfile
from bot.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from utils.vpn_parser import is_valid_vpn_uri
from utils.admin import is_admin

logger = logging.getLogger(__name__)
MSK_TZ = ZoneInfo("Europe/Moscow")

class DeviceCreationError(Exception): pass
class DailyLimitExceeded(DeviceCreationError): pass
class DeviceLimitExceeded(DeviceCreationError): pass
class ServerUnavailable(DeviceCreationError): pass
class InvalidConfig(DeviceCreationError): pass

_user_locks: dict[int, asyncio.Lock] = {}

def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]

def _is_same_day_msk(stored_date: date | None, now_msk: date) -> bool:
    if stored_date is None:
        return False
    return stored_date == now_msk

class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession, user: User, server_id: int, device_name: str
    ) -> VPNProfile:
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            logger.warning(
                f"create_device: invalid server {server_id} or protocol mismatch"
            )
            raise ServerUnavailable("Invalid server or protocol")

        # 🔥 СКРЫТАЯ УЯЗВИМОСТЬ #11: force_refresh=True для защиты от race condition
        # Раньше: force_refresh=False (кэш 5 мин). Если 10 юзеров одновременно нажмут
        # "Создать устройство" на сервере с 1 свободным слотом, все пройдут проверку.
        # Теперь: всегда запрашиваем API перед созданием для точности.
        real_count = await get_real_peer_count(server, force_refresh=True)

        if real_count != -1 and real_count >= server.max_clients:
            logger.warning(f"create_device: server {server.name} is full")
            raise ServerUnavailable("Server is full")

        lock = _get_user_lock(user.id)
        async with lock:
            # 🔥 ИСПРАВЛЕНО: SELECT FOR UPDATE для защиты от race condition
            result = await session.execute(
                select(User)
                .where(User.telegram_id == user.telegram_id)
                .with_for_update()
            )
            user = result.scalar_one_or_none()
            if not user:
                raise ServerUnavailable("User disappeared")

            if not is_admin(user.telegram_id):
                now_msk = datetime.now(MSK_TZ).date()
                if not _is_same_day_msk(user.last_creation_date, now_msk):
                    user.device_creations_today = 0
                    user.last_creation_date = now_msk
                    await session.flush()

                if user.device_creations_today >= DEVICE_DAILY_LIMIT:
                    logger.warning(
                        f"create_device: user {user.telegram_id} exceeded daily limit"
                    )
                    try:
                        await AuditService.log_action(
                            session, admin_id=0, action="DEVICE_CREATE_BLOCKED",
                            target_type="User", target_id=user.telegram_id,
                            details=f"Daily limit: {user.device_creations_today}/{DEVICE_DAILY_LIMIT}"
                        )
                    except Exception as audit_error:
                        logger.error(
                            f"Failed to log DEVICE_CREATE_BLOCKED: {audit_error}"
                        )
                    raise DailyLimitExceeded("Daily limit exceeded")

            short_hash = uuid.uuid4().hex[:4]
            clean_device_name = re.sub(r'[^a-zA-Z0-9]', '', device_name)[:10]
            if not clean_device_name:
                clean_device_name = "Device"

            client_name = f"tg_{user.telegram_id}_{clean_device_name}_{short_hash}"

            expires_ts = await SubscriptionService.get_expires_timestamp(user)

            client = AmneziaClient(server.api_url, server.api_key)
            result = await client.create_user(
                client_name=client_name, expires_at=expires_ts
            )

            if not result:
                raise ServerUnavailable("API create_user failed")

            peer_id = result.id
            raw_config = result.config

            if not is_valid_vpn_uri(raw_config):
                logger.error(
                    f"create_device: API returned invalid vpn:// URI. Rolling back."
                )
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(
                        f"Failed to rollback invalid config: {rollback_error}"
                    )
                raise InvalidConfig("Invalid vpn:// URI")

            try:
                async with session.begin_nested() as savepoint:
                    profiles_count = await get_user_profiles_count(session, user.id)

                    if profiles_count >= user.device_limit:
                        await savepoint.rollback()
                        try:
                            await client.delete_user(client_id=peer_id)
                        except Exception as rollback_error:
                            logger.error(
                                f"Failed to rollback API client after limit check: "
                                f"{rollback_error}"
                            )
                        raise DeviceLimitExceeded("Device limit reached")

                    profile = await create_profile(
                        session, user_id=user.id, server_id=server.id,
                        device_name=device_name, peer_id=peer_id,
                        raw_config=raw_config
                    )

                    if not is_admin(user.telegram_id):
                        user.device_creations_today += 1

                    await savepoint.commit()

                    try:
                        await AuditService.log_action(
                            session, admin_id=user.telegram_id,
                            action="DEVICE_CREATED",
                            target_type="VPNProfile", target_id=profile.id,
                            details=f"user={user.telegram_id}, device={device_name}, server={server.name}"
                        )
                    except Exception as audit_error:
                        logger.warning(
                            f"Failed to log DEVICE_CREATED: {audit_error}"
                        )

                    return profile

            except IntegrityError as e:
                await session.rollback()
                logger.error(f"create_device: IntegrityError: {e}")
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(
                        f"Failed to rollback after IntegrityError: {rollback_error}"
                    )
                raise
            except (DailyLimitExceeded, DeviceLimitExceeded, InvalidConfig, ServerUnavailable):
                raise
            except Exception as e:
                await session.rollback()
                logger.error(f"create_device: DB error: {e}", exc_info=True)
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(
                        f"Failed to rollback after DB error: {rollback_error}"
                    )
                raise ServerUnavailable(f"DB error: {e}")

    @staticmethod
    async def delete_device(
        session: AsyncSession, profile: VPNProfile
    ) -> bool:
        from database.repositories.profiles_repo import delete_profile
        server = await get_server_by_id(session, profile.server_id)
        if not server:
            logger.error(
                f"delete_device: server {profile.server_id} not found"
            )
            return False

        client = AmneziaClient(server.api_url, server.api_key)
        deleted = await client.delete_user(client_id=profile.peer_id)
        if not deleted:
            logger.error(
                f"delete_device: API delete_user failed for "
                f"peer_id={profile.peer_id[:16]}..."
            )
            return False

        try:
            async with session.begin_nested() as savepoint:
                await delete_profile(session, profile)
                await savepoint.commit()

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=profile.user.telegram_id if hasattr(profile, 'user') else 0,
                        action="DEVICE_DELETED",
                        target_type="VPNProfile", target_id=profile.id,
                        details=f"device={profile.device_name}, server={server.name}"
                    )
                except Exception as audit_error:
                    logger.warning(
                        f"Failed to log DEVICE_DELETED: {audit_error}"
                    )
                return True
        except Exception as e:
            await session.rollback()
            logger.error(f"delete_device: DB error: {e}", exc_info=True)
            return False