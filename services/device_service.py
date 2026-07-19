import uuid
import re
import logging
import asyncio
from datetime import date

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from services.amnezia_client import AmneziaClient
from services.subscription import SubscriptionService
from services.audit_service import AuditService
from services.slots_cache import get_real_peer_count
from database.repositories.profiles_repo import (
    create_profile,
    get_user_profiles_count,
)
from database.repositories.servers_repo import get_server_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.models import User, VPNProfile
from bot.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from utils.vpn_parser import is_valid_vpn_uri
from utils.admin import is_admin
from utils.datetime_helpers import now_msk
from config.settings import get_settings

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CRITICAL_SLOTS_THRESHOLD = 5

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis_client


class DeviceCreationError(Exception):
    pass


class DailyLimitExceeded(DeviceCreationError):
    pass


class DeviceLimitExceeded(DeviceCreationError):
    pass


class ServerUnavailable(DeviceCreationError):
    pass


class InvalidConfig(DeviceCreationError):
    pass


_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _is_same_day_msk(
    stored_date: date | None, now_msk_date: date,
) -> bool:
    if stored_date is None:
        return False
    return stored_date == now_msk_date


async def _get_server_profiles_count(
    session: AsyncSession, server_id: int,
) -> int:
    stmt = select(func.count(VPNProfile.id)).where(
        VPNProfile.server_id == server_id,
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0


class DeviceService:

    @staticmethod
    async def create_device(
        session: AsyncSession,
        user: User,
        server_id: int,
        device_name: str,
    ) -> VPNProfile:
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            logger.warning(
                f"create_device: invalid server {server_id} "
                f"or protocol mismatch"
            )
            raise ServerUnavailable("Invalid server or protocol")

        if not server.is_active:
            logger.warning(
                f"create_device: server {server.name} "
                f"(id={server.id}) is disabled"
            )
            raise ServerUnavailable("Server is disabled by admin")

        redis = await _get_redis()
        lock_key = f"lock:create_device:server:{server.id}"
        redis_lock = redis.lock(
            lock_key, timeout=30, blocking_timeout=5,
        )

        try:
            acquired = await redis_lock.acquire()
            if not acquired:
                logger.warning(
                    f"create_device: failed to acquire Redis lock "
                    f"for server {server.id}"
                )
                raise ServerUnavailable("Server is busy, try again")

            local_count = await _get_server_profiles_count(
                session, server.id,
            )
            free_slots = server.max_clients - local_count

            if free_slots <= 0:
                logger.warning(
                    f"create_device: server {server.name} is full "
                    f"(local_count={local_count}/"
                    f"{server.max_clients})"
                )
                raise ServerUnavailable("Server is full")

            if free_slots < CRITICAL_SLOTS_THRESHOLD:
                logger.info(
                    f"create_device: critical zone on "
                    f"{server.name}, checking API for accuracy "
                    f"(local free={free_slots}"
                    f"<{CRITICAL_SLOTS_THRESHOLD})"
                )
                real_count = await get_real_peer_count(
                    server, force_refresh=True,
                )
                if (
                    real_count != -1
                    and real_count >= server.max_clients
                ):
                    logger.warning(
                        f"create_device: server {server.name} "
                        f"is full (api_count={real_count}/"
                        f"{server.max_clients})"
                    )
                    raise ServerUnavailable("Server is full")

            lock = _get_user_lock(user.id)
            async with lock:
                result = await session.execute(
                    select(User)
                    .where(User.telegram_id == user.telegram_id)
                    .with_for_update()
                )
                user = result.scalar_one_or_none()
                if not user:
                    raise ServerUnavailable("User disappeared")

                if not is_admin(user.telegram_id):
                    now_msk_date = now_msk().date()
                    if not _is_same_day_msk(
                        user.last_creation_date, now_msk_date,
                    ):
                        user.device_creations_today = 0
                        user.last_creation_date = now_msk_date
                        await session.flush()

                    if (
                        user.device_creations_today
                        >= DEVICE_DAILY_LIMIT
                    ):
                        logger.warning(
                            f"create_device: user "
                            f"{user.telegram_id} exceeded "
                            f"daily limit"
                        )
                        try:
                            await AuditService.log_action(
                                session,
                                admin_id=0,
                                action="DEVICE_CREATE_BLOCKED",
                                target_type="User",
                                target_id=user.telegram_id,
                                details=(
                                    f"Daily limit: "
                                    f"{user.device_creations_today}"
                                    f"/{DEVICE_DAILY_LIMIT}"
                                ),
                            )
                        except Exception as audit_error:
                            logger.error(
                                f"Failed to log "
                                f"DEVICE_CREATE_BLOCKED: "
                                f"{audit_error}"
                            )
                        raise DailyLimitExceeded(
                            "Daily limit exceeded",
                        )

                short_hash = uuid.uuid4().hex[:4]
                clean_device_name = re.sub(
                    r'[^a-zA-Z0-9]', '', device_name,
                )[:10]
                if not clean_device_name:
                    clean_device_name = "Device"

                client_name = (
                    f"tg_{user.telegram_id}_"
                    f"{clean_device_name}_{short_hash}"
                )

                expires_ts = (
                    await SubscriptionService.get_expires_timestamp(
                        user,
                    )
                )

                client = AmneziaClient(
                    server.api_url, server.api_key,
                )
                api_result = await client.create_user(
                    client_name=client_name,
                    expires_at=expires_ts,
                )

                if not api_result:
                    raise ServerUnavailable(
                        "API create_user failed",
                    )

                peer_id = api_result.id
                raw_config = api_result.config

                if not is_valid_vpn_uri(raw_config):
                    logger.error(
                        "create_device: API returned invalid "
                        "vpn:// URI. Rolling back."
                    )
                    try:
                        await client.delete_user(
                            client_id=peer_id,
                        )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback invalid "
                            f"config: {rollback_error}"
                        )
                    raise InvalidConfig("Invalid vpn:// URI")

                try:
                    async with session.begin_nested():
                        profiles_count = (
                            await get_user_profiles_count(
                                session, user.id,
                            )
                        )
                        if profiles_count >= user.device_limit:
                            raise DeviceLimitExceeded(
                                "Device limit reached",
                            )

                        profile = await create_profile(
                            session,
                            user_id=user.id,
                            server_id=server.id,
                            device_name=device_name,
                            peer_id=peer_id,
                            raw_config=raw_config,
                        )

                        if not is_admin(user.telegram_id):
                            user.device_creations_today += 1

                        try:
                            await AuditService.log_action(
                                session,
                                admin_id=user.telegram_id,
                                action="DEVICE_CREATED",
                                target_type="VPNProfile",
                                target_id=profile.id,
                                details=(
                                    f"user={user.telegram_id}, "
                                    f"device={device_name}, "
                                    f"server={server.name}"
                                ),
                            )
                        except Exception as audit_error:
                            logger.warning(
                                f"Failed to log DEVICE_CREATED: "
                                f"{audit_error}"
                            )

                        return profile

                except DeviceLimitExceeded:
                    try:
                        await client.delete_user(
                            client_id=peer_id,
                        )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback API client "
                            f"after limit check: "
                            f"{rollback_error}"
                        )
                    raise

                except IntegrityError as e:
                    await session.rollback()
                    logger.error(
                        f"create_device: IntegrityError: {e}"
                    )
                    try:
                        await client.delete_user(
                            client_id=peer_id,
                        )
                    except Exception as rollback_error:
                        logger.error(
                            f"Failed to rollback after "
                            f"IntegrityError: {rollback_error}"
                        )
                    raise

                except (
                    DailyLimitExceeded,
                    DeviceLimitExceeded,
                    InvalidConfig,
                    ServerUnavailable,
                ):
                    raise

                except Exception as e:
                    await session.rollback()
                    logger.error(
                        f"create_device: DB error: {e}",
                        exc_info=True,
                    )
                    raise ServerUnavailable(f"DB error: {e}")

        finally:
            try:
                await redis_lock.release()
            except Exception:
                pass

    @staticmethod
    async def delete_device(
        session: AsyncSession, profile: VPNProfile,
    ) -> bool:
        from database.repositories.profiles_repo import (
            delete_profile,
        )

        server = await get_server_by_id(
            session, profile.server_id,
        )
        if not server:
            logger.error(
                f"delete_device: server "
                f"{profile.server_id} not found"
            )
            return False

        client = AmneziaClient(server.api_url, server.api_key)
        deleted = await client.delete_user(
            client_id=profile.peer_id,
        )

        if not deleted:
            logger.warning(
                f"delete_device: API delete_user returned False "
                f"for peer_id={profile.peer_id[:16]}... "
                f"(peer may already be deleted on API or API "
                f"unavailable). Proceeding with DB deletion. "
                f"Cleanup worker will remove orphaned peers."
            )

        try:
            async with session.begin_nested():
                await delete_profile(session, profile)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=(
                            profile.user.telegram_id
                            if hasattr(profile, 'user')
                            and profile.user
                            else 0
                        ),
                        action="DEVICE_DELETED",
                        target_type="VPNProfile",
                        target_id=profile.id,
                        details=(
                            f"device={profile.device_name}, "
                            f"server={server.name}"
                        ),
                    )
                except Exception as audit_error:
                    logger.warning(
                        f"Failed to log DEVICE_DELETED: "
                        f"{audit_error}"
                    )

                return True

        except Exception as e:
            await session.rollback()
            logger.error(
                f"delete_device: DB error: {e}",
                exc_info=True,
            )
            return False