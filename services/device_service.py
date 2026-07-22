import asyncio
import logging
import re
import uuid
from datetime import date

from database.connection import session_scope
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from bot.constants import AMNEZIA_PROTOCOL, DEVICE_DAILY_LIMIT
from config.settings import get_settings
from database.models import (
    PendingAPIDeletion,
    User,
    VPNProfile,
)
from database.repositories.profiles_repo import (
    create_profile,
    delete_profile,
    get_user_profiles_count,
)
from database.repositories.servers_repo import get_server_by_id
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService
from services.slots_cache import get_real_peer_count
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.datetime_helpers import now_msk, now_utc, is_expired
from utils.vpn_parser import is_valid_vpn_uri, build_conf_file

logger = logging.getLogger(__name__)

CRITICAL_SLOTS_THRESHOLD = 5

_redis_client: aioredis.Redis | None = None
_server_locks: dict[int, asyncio.Lock] = {}


def _get_server_lock(server_id: int) -> asyncio.Lock:
    if server_id not in _server_locks:
        _server_locks[server_id] = asyncio.Lock()
    return _server_locks[server_id]


async def _queue_failed_rollback_deletion(
    server,
    peer_id: str,
    user_id: int,
    reason: str,
) -> None:
    """
    Если rollback-удаление пира не удалось, ставим его в pending.
    Используем отдельную сессию, чтобы не зависеть от текущего rollback.
    """
    if not server.api_url or not server.api_key:
        return

    try:
        async with session_scope() as session:
            pending = PendingAPIDeletion(
                server_name=server.name,
                api_url=server.api_url,
                api_key=server.api_key,
                peer_id=peer_id,
                client_name=f"tg_{user_id}_rollback",
                reason="create_device_rollback_failed",
                attempts=1,
                last_attempt_at=now_utc(),
                last_error=reason,
            )
            session.add(pending)
            await session.flush()
    except Exception as e:
        logger.error(
            "Failed to queue rollback pending deletion: %s",
            e,
            exc_info=True,
        )


async def close_redis() -> None:
    global _redis_client

    if _redis_client is not None:
        try:
            await _redis_client.close()
        finally:
            _redis_client = None


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


class NoActiveSubscription(DeviceCreationError):
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
    stored_date: date | None,
    now_msk_date: date,
) -> bool:
    if stored_date is None:
        return False
    return stored_date == now_msk_date


async def _get_server_profiles_count(
    session: AsyncSession,
    server_id: int,
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
                "create_device: invalid server %s "
                "or protocol mismatch",
                server_id,
            )
            raise ServerUnavailable("Invalid server or protocol")

        if not server.is_active:
            logger.warning(
                "create_device: server %s (id=%s) is disabled",
                server.name,
                server.id,
            )
            raise ServerUnavailable("Server is disabled by admin")

        #
        # Сервисный запрет создания устройства без активной подписки.
        # Это дополнительная защита даже если handler по какой-то причине
        # пропустил проверку.
        #
        if not await SubscriptionService.check_access(
            session,
            user.telegram_id,
        ):
            logger.warning(
                "create_device: user %s has no active subscription",
                user.telegram_id,
            )
            raise NoActiveSubscription("No active subscription")

        redis_lock = None
        local_lock = None
        acquired = False
        using_redis = False

        peer_id = None
        client = None

        try:
            try:
                redis = await _get_redis()
                lock_key = f"lock:create_device:server:{server.id}"

                #
                # Увеличенный timeout Redis-lock.
                # Раньше было 30 секунд, что могло быть мало при медленном API
                # и повторных попытках.
                #
                redis_lock = redis.lock(
                    lock_key,
                    timeout=60,
                    blocking_timeout=10,
                )
                acquired = await redis_lock.acquire()
                using_redis = True

            except Exception as e:
                logger.warning(
                    "create_device: Redis lock unavailable, "
                    "falling back to local lock: %s",
                    e,
                )
                local_lock = _get_server_lock(server.id)
                await local_lock.acquire()
                acquired = True
                using_redis = False

            if not acquired:
                logger.warning(
                    "create_device: failed to acquire lock "
                    "for server %s",
                    server.id,
                )
                raise ServerUnavailable("Server is busy, try again")

            local_count = await _get_server_profiles_count(
                session,
                server.id,
            )

            free_slots = server.max_clients - local_count

            if free_slots <= 0:
                logger.warning(
                    "create_device: server %s is full "
                    "(local_count=%s/%s)",
                    server.name,
                    local_count,
                    server.max_clients,
                )
                raise ServerUnavailable("Server is full")

            if free_slots < CRITICAL_SLOTS_THRESHOLD:
                logger.info(
                    "create_device: critical zone on %s, "
                    "checking API for accuracy (local free=%s<%s)",
                    server.name,
                    free_slots,
                    CRITICAL_SLOTS_THRESHOLD,
                )

                real_count = await get_real_peer_count(
                    server,
                    force_refresh=True,
                )

                if real_count == -1:
                    logger.warning(
                        "create_device: cannot verify real peer count "
                        "for server %s in critical zone. "
                        "Blocking creation to avoid overfill.",
                        server.name,
                    )
                    raise ServerUnavailable(
                        "Cannot verify server slots, try later",
                    )

                if real_count >= server.max_clients:
                    logger.warning(
                        "create_device: server %s is full "
                        "(api_count=%s/%s)",
                        server.name,
                        real_count,
                        server.max_clients,
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

                #
                # Повторная проверка подписки уже под блокировкой.
                # Это защищает от гонки, если подписка истекла прямо
                # во время создания устройства.
                #
                if (
                    user.is_banned
                    or not user.subscription_end
                    or is_expired(user.subscription_end)
                ):
                    logger.warning(
                        "create_device: user %s blocked or subscription "
                        "is not active after lock",
                        user.telegram_id,
                    )
                    raise NoActiveSubscription("No active subscription")

                if not is_admin(user.telegram_id):
                    now_msk_date = now_msk().date()

                    if not _is_same_day_msk(
                        user.last_creation_date,
                        now_msk_date,
                    ):
                        user.device_creations_today = 0
                        user.last_creation_date = now_msk_date
                        await session.flush()

                    if (
                        user.device_creations_today
                        >= DEVICE_DAILY_LIMIT
                    ):
                        logger.warning(
                            "create_device: user %s exceeded "
                            "daily limit",
                            user.telegram_id,
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
                                "Failed to log "
                                "DEVICE_CREATE_BLOCKED: %s",
                                audit_error,
                            )

                        raise DailyLimitExceeded(
                            "Daily limit exceeded",
                        )

                short_hash = uuid.uuid4().hex[:4]

                clean_device_name = re.sub(
                    r"[^a-zA-Z0-9]",
                    "",
                    device_name,
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
                    server.api_url,
                    server.api_key,
                )

                try:
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

                    #
                    # Проверяем, что API вернул валидный vpn:// URI.
                    #
                    if not is_valid_vpn_uri(raw_config):
                        logger.error(
                            "create_device: API returned invalid "
                            "configuration URI. Rolling back."
                        )

                        try:
                            deleted = await client.delete_user(
                                client_id=peer_id,
                            )

                            if not deleted:
                                await _queue_failed_rollback_deletion(
                                    server,
                                    peer_id,
                                    user.telegram_id,
                                    "invalid_config_delete_false",
                                )

                        except Exception as rollback_error:
                            await _queue_failed_rollback_deletion(
                                server,
                                peer_id,
                                user.telegram_id,
                                f"invalid_config_delete_error: {rollback_error}",
                            )

                        raise InvalidConfig(
                            "Invalid configuration URI"
                        )

                    #
                    # Проверяем, что из vpn:// реально достаётся готовый .conf.
                    # Если конфиг битый, откатываем создание пира.
                    #
                    conf_content = build_conf_file(raw_config)

                    if not conf_content:
                        logger.error(
                            "create_device: API returned vpn:// URI "
                            "without usable .conf content. Rolling back."
                        )

                        try:
                            deleted = await client.delete_user(
                                client_id=peer_id,
                            )

                            if not deleted:
                                await _queue_failed_rollback_deletion(
                                    server,
                                    peer_id,
                                    user.telegram_id,
                                    "broken_conf_delete_false",
                                )

                        except Exception as rollback_error:
                            await _queue_failed_rollback_deletion(
                                server,
                                peer_id,
                                user.telegram_id,
                                f"broken_conf_delete_error: {rollback_error}",
                            )

                        raise InvalidConfig(
                            "Broken configuration content"
                        )

                    try:
                        async with session.begin_nested():
                            profiles_count = (
                                await get_user_profiles_count(
                                    session,
                                    user.id,
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
                                    "Failed to log DEVICE_CREATED: %s",
                                    audit_error,
                                )

                            return profile

                    except DeviceLimitExceeded:
                        try:
                            deleted = await client.delete_user(
                                client_id=peer_id,
                            )

                            if not deleted:
                                await _queue_failed_rollback_deletion(
                                    server,
                                    peer_id,
                                    user.telegram_id,
                                    "device_limit_delete_false",
                                )

                        except Exception as rollback_error:
                            await _queue_failed_rollback_deletion(
                                server,
                                peer_id,
                                user.telegram_id,
                                f"device_limit_delete_error: {rollback_error}",
                            )

                        raise

                    except IntegrityError as e:
                        await session.rollback()

                        logger.error(
                            "create_device: IntegrityError: %s",
                            e,
                        )

                        try:
                            deleted = await client.delete_user(
                                client_id=peer_id,
                            )

                            if not deleted:
                                await _queue_failed_rollback_deletion(
                                    server,
                                    peer_id,
                                    user.telegram_id,
                                    "integrity_error_delete_false",
                                )

                        except Exception as rollback_error:
                            await _queue_failed_rollback_deletion(
                                server,
                                peer_id,
                                user.telegram_id,
                                f"integrity_error_delete_error: {rollback_error}",
                            )

                        raise

                except (
                    DailyLimitExceeded,
                    DeviceLimitExceeded,
                    InvalidConfig,
                    ServerUnavailable,
                    NoActiveSubscription,
                ):
                    raise

                except Exception as e:
                    await session.rollback()

                    logger.error(
                        "create_device: DB error: %s",
                        e,
                        exc_info=True,
                    )

                    if peer_id and client:
                        try:
                            deleted = await client.delete_user(
                                client_id=peer_id,
                            )

                            if not deleted:
                                await _queue_failed_rollback_deletion(
                                    server,
                                    peer_id,
                                    user.telegram_id,
                                    "unexpected_error_delete_false",
                                )

                        except Exception as rollback_error:
                            await _queue_failed_rollback_deletion(
                                server,
                                peer_id,
                                user.telegram_id,
                                f"unexpected_error_delete_error: {rollback_error}",
                            )

                    raise ServerUnavailable(f"DB error: {e}")

        finally:
            if using_redis and redis_lock is not None and acquired:
                try:
                    await redis_lock.release()
                except Exception:
                    pass

            elif local_lock is not None and acquired:
                try:
                    local_lock.release()
                except Exception:
                    pass

    @staticmethod
    async def delete_device(
        session: AsyncSession,
        profile: VPNProfile,
    ) -> bool:
        server = await get_server_by_id(
            session,
            profile.server_id,
        )

        if not server:
            logger.error(
                "delete_device: server %s not found for profile %s. "
                "Deleting profile from DB only.",
                profile.server_id,
                profile.id,
            )

            try:
                await delete_profile(session, profile)
                return True
            except Exception as e:
                await session.rollback()
                logger.error(
                    "delete_device: failed to delete profile %s "
                    "from DB: %s",
                    profile.id,
                    e,
                )
                return False

        client = AmneziaClient(
            server.api_url,
            server.api_key,
        )

        deleted = await client.delete_user(
            client_id=profile.peer_id,
        )

        if not deleted:
            logger.warning(
                "delete_device: API delete_user returned False "
                "for peer_id=%s... (peer may already be deleted "
                "or API unavailable). Proceeding with DB deletion. "
                "Queuing pending API deletion.",
                profile.peer_id[:16],
            )

            if server.api_url and server.api_key:
                pending = PendingAPIDeletion(
                    server_name=server.name,
                    api_url=server.api_url,
                    api_key=server.api_key,
                    peer_id=profile.peer_id,
                    client_name=(
                        f"tg_{profile.user_id}_{profile.id}"
                    ),
                    reason="device_delete_api_failed",
                    attempts=1,
                    last_attempt_at=now_utc(),
                    last_error="API delete_user returned False",
                )
                session.add(pending)

        try:
            async with session.begin_nested():
                await delete_profile(session, profile)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=profile.user_id,
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
                        "Failed to log DEVICE_DELETED: %s",
                        audit_error,
                    )

                return True

        except Exception as e:
            await session.rollback()
            logger.error(
                "delete_device: DB error: %s",
                e,
                exc_info=True,
            )
            return False