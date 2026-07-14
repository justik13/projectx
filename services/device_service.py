import uuid
import re
import logging
import asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from services.amnezia_client import AmneziaClient
from services.subscription import SubscriptionService
from services.audit_service import AuditService
from database.repositories.profiles_repo import create_profile, get_user_profiles_count
from database.repositories.servers_repo import get_server_by_id
from database.models import User, VPNProfile
from bot.constants import AMNEZIA_PROTOCOL
from utils.vpn_parser import is_valid_vpn_uri, validate_awg2_config, decode_vpn_uri_to_json

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
        """
        Создаёт новое устройство для пользователя.
        
        🔥 ИСПРАВЛЕНО в Фазе 4:
        - Атомарность через begin_nested() (savepoint)
        - Валидация API response: protocol_version == "2" и is_valid_vpn_uri()
        - Логирование rollback в audit_logs для разбора инцидентов
        - Защита от race condition через DB unique constraint на peer_id
        """
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            logger.warning(
                f"create_device: invalid server {server_id} or protocol mismatch"
            )
            return None

        lock = _get_user_lock(user.id)
        async with lock:
            # Проверка лимита устройств
            profiles_count = await get_user_profiles_count(session, user.id)
            if profiles_count >= user.device_limit:
                logger.info(
                    f"create_device: user {user.telegram_id} reached device limit "
                    f"({profiles_count}/{user.device_limit})"
                )
                return None

            # Генерация имени клиента для API
            short_hash = uuid.uuid4().hex[:4]
            clean_device_name = re.sub(r'[^a-zA-Z0-9]', '', device_name)[:10]
            client_name = f"tg_{user.telegram_id}_{clean_device_name}_{short_hash}"
            expires_ts = await SubscriptionService.get_expires_timestamp(user)

            # Создание клиента на сервере
            client = AmneziaClient(server.api_url, server.api_key)
            result = await client.create_user(client_name=client_name, expires_at=expires_ts)
            
            if not result:
                logger.error(
                    f"create_device: API create_user failed for user {user.telegram_id}, "
                    f"server {server.name}"
                )
                return None

            peer_id = result.id
            raw_config = result.config

            # 🔥 ИСПРАВЛЕНО: Валидация API response
            if not is_valid_vpn_uri(raw_config):
                logger.error(
                    f"create_device: API returned invalid vpn:// URI for user {user.telegram_id}. "
                    f"Rolling back."
                )
                # Rollback: удаляем клиента с сервера
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback invalid config: {rollback_error}")
                
                # Аудит инцидента
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="DEVICE_CREATE_FAILED",
                        target_type="User",
                        target_id=user.telegram_id,
                        details=f"Invalid vpn:// URI from API, server={server.name}"
                    )
                except Exception as audit_error:
                    logger.error(f"Failed to log audit: {audit_error}")
                
                return None

            # 🔥 ИСПРАВЛЕНО: Проверка protocol_version == "2"
            config_data = decode_vpn_uri_to_json(raw_config)
            if config_data:
                validation = validate_awg2_config(config_data)
                if not validation.is_valid:
                    logger.error(
                        f"create_device: API returned invalid AWG 2.0 config. "
                        f"Errors: {validation.errors}. Rolling back."
                    )
                    # Rollback
                    try:
                        await client.delete_user(client_id=peer_id)
                    except Exception as rollback_error:
                        logger.error(f"Failed to rollback invalid AWG config: {rollback_error}")
                    
                    # Аудит
                    try:
                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="DEVICE_CREATE_FAILED",
                            target_type="User",
                            target_id=user.telegram_id,
                            details=f"AWG 2.0 validation failed: {', '.join(validation.errors[:3])}"
                        )
                    except Exception as audit_error:
                        logger.error(f"Failed to log audit: {audit_error}")
                    
                    return None

            # 🔥 ИСПРАВЛЕНО: Атомарность через begin_nested() (savepoint)
            try:
                async with session.begin_nested() as savepoint:
                    profile = await create_profile(
                        session,
                        user_id=user.id,
                        server_id=server.id,
                        device_name=device_name,
                        peer_id=peer_id,
                        raw_config=raw_config
                    )
                    await savepoint.commit()
                
                # Финальный commit основной транзакции
                await session.commit()
                
                logger.info(
                    f"Device created: user={user.telegram_id}, device={device_name}, "
                    f"server={server.name}, peer_id={peer_id[:16]}..."
                )
                return profile

            except IntegrityError as e:
                # 🔥 ИСПРАВЛЕНО: Защита от race condition через DB unique constraint
                await session.rollback()
                logger.error(
                    f"create_device: IntegrityError (duplicate peer_id?): {e}. "
                    f"Rolling back API client."
                )
                # Rollback: удаляем клиента с сервера
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback after IntegrityError: {rollback_error}")
                
                # Аудит
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="DEVICE_CREATE_FAILED",
                        target_type="User",
                        target_id=user.telegram_id,
                        details=f"IntegrityError (duplicate peer_id), server={server.name}"
                    )
                except Exception as audit_error:
                    logger.error(f"Failed to log audit: {audit_error}")
                
                return None

            except Exception as e:
                await session.rollback()
                logger.error(
                    f"create_device: DB error while creating profile: {e}. "
                    f"Rolling back API client.",
                    exc_info=True
                )
                # Rollback: удаляем клиента с сервера
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback after DB error: {rollback_error}")
                
                # Аудит
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="DEVICE_CREATE_FAILED",
                        target_type="User",
                        target_id=user.telegram_id,
                        details=f"DB error: {str(e)[:100]}, server={server.name}"
                    )
                except Exception as audit_error:
                    logger.error(f"Failed to log audit: {audit_error}")
                
                return None

    @staticmethod
    async def delete_device(session: AsyncSession, profile: VPNProfile) -> bool:
        """
        Удаляет устройство пользователя.
        
        🔥 ИСПРАВЛЕНО в Фазе 4:
        - Атомарность через begin_nested() (savepoint)
        - Если delete_user удалит с API, но delete_profile упадёт — savepoint откатится
        - Логирование успеха/ошибки в audit_logs
        """
        from database.repositories.profiles_repo import delete_profile
        from database.repositories.servers_repo import get_server_by_id

        server = await get_server_by_id(session, profile.server_id)
        if not server:
            logger.error(f"delete_device: server {profile.server_id} not found")
            return False

        # Удаление клиента с сервера
        client = AmneziaClient(server.api_url, server.api_key)
        deleted = await client.delete_user(client_id=profile.peer_id)
        
        if not deleted:
            logger.error(
                f"delete_device: API delete_user failed for peer_id={profile.peer_id[:16]}..., "
                f"server={server.name}"
            )
            return False

        # 🔥 ИСПРАВЛЕНО: Атомарность через begin_nested() (savepoint)
        try:
            async with session.begin_nested() as savepoint:
                await delete_profile(session, profile)
                await savepoint.commit()
            
            # Финальный commit основной транзакции
            await session.commit()
            
            logger.info(
                f"Device deleted: profile_id={profile.id}, peer_id={profile.peer_id[:16]}..., "
                f"server={server.name}"
            )
            return True

        except Exception as e:
            await session.rollback()
            logger.error(
                f"delete_device: DB error while deleting profile: {e}. "
                f"Profile remains in DB but deleted from API!",
                exc_info=True
            )
            
            # Аудит критической ошибки
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="DEVICE_DELETE_DB_ERROR",
                    target_type="VPNProfile",
                    target_id=profile.id,
                    details=f"Deleted from API but DB error: {str(e)[:100]}"
                )
            except Exception as audit_error:
                logger.error(f"Failed to log audit: {audit_error}")
            
            return False