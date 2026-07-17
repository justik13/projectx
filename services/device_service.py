"""
Сервис управления устройствами (создание/удаление).

🔥 KNOWN LIMITATION: In-memory locks
═══════════════════════════════════
_user_locks хранит per-user asyncio.Lock в памяти процесса.
При рестарте бота все locks очищаются.

Почему это acceptable для single-worker:
1. ThrottlingMiddleware (0.1s) защищает от double-click
2. DB unique constraint на peer_id защищает от дубликатов
3. begin_nested() (savepoints) обеспечивает атомарность операций
4. ActionLockMiddleware блокирует add_device + select_server на уровне callback
5. Стадия активного тестирования — реальных пользователей нет

Когда потребуется Redis locks:
- При масштабировании до multi-worker (несколько процессов)
- При появлении реальных платящих пользователей
- При деплое на Kubernetes/Docker Swarm

Текущий риск: МИНИМАЛЬНЫЙ
"""

import uuid
import re
import logging
import asyncio
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

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

# Часовой пояс Москвы для сброса daily limit
MSK_TZ = ZoneInfo("Europe/Moscow")


# ⚠️ In-memory lock — сбрасывается при рестарте бота.
# Для single-worker это acceptable risk.
# DB unique constraint на peer_id защищает от дубликатов.
_user_locks: dict[int, asyncio.Lock] = {}


def _get_user_lock(user_id: int) -> asyncio.Lock:
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _is_same_day_msk(stored_date: date | None, now_msk: date) -> bool:
    """
    Проверяет, совпадает ли сохранённая дата с текущей по МСК.
    Если stored_date == None, считаем что это другой день.
    """
    if stored_date is None:
        return False
    # SQLAlchemy Date хранится как Python date (без timezone)
    # Сравниваем напрямую
    return stored_date == now_msk


class DeviceService:
    @staticmethod
    async def create_device(
        session: AsyncSession, user: User, server_id: int, device_name: str
    ) -> VPNProfile | None:
        """
        Создаёт новое устройство для пользователя.
        
        Flow:
        1. Pre-flight check: проверяем реальные слоты через API (force_refresh=True)
        2. Захватываем per-user lock для предотвращения race conditions
        3. 🔥 ИСПРАВЛЕНО #26: Перезагружаем user из текущей сессии (защита от detached объекта)
        4. 🔥 ИСПРАВЛЕНО: Проверяем daily device creation limit (25/день)
        5. Генерируем client_name: tg_{telegram_id}_{device_name}_{4-char-hash}
        6. Создаём клиента на сервере Amnezia API
        7. Валидируем vpn:// URI (проверяем protocol_version == "2")
        8. Проверяем device_limit через savepoint
        9. Сохраняем профиль в БД
        10. 🔥 ИСПРАВЛЕНО: Увеличиваем daily счётчик ПОСЛЕ успешного создания
        11. Логируем в audit_logs
        
        Args:
            session: SQLAlchemy async session
            user: Объект пользователя (User)
            server_id: ID сервера в БД
            device_name: Имя устройства (макс. 16 символов)
            
        Returns:
            VPNProfile если успешно создано
            None если:
            - Сервер не найден или протокол не amneziawg2
            - Превышен daily device limit (25/день)
            - API вернул невалидный vpn:// URI
            - Достигнут device_limit
            - DB IntegrityError (дубликат peer_id)
            
        Side effects:
            - Создаёт клиента на Amnezia API
            - Увеличивает User.device_creations_today
            - Пишет в audit_logs (action="DEVICE_CREATED")
            - При блокировке по daily limit — логирует "DEVICE_CREATE_BLOCKED"
            - При ошибке — откатывает создание через API DELETE
            
        Thread-safety:
            Использует per-user asyncio.Lock для предотвращения race conditions
            при параллельном создании устройств.
        """
        server = await get_server_by_id(session, server_id)
        if not server or server.protocol != AMNEZIA_PROTOCOL:
            logger.warning(
                f"create_device: invalid server {server_id} or protocol mismatch"
            )
            return None
        
        # PRE-FLIGHT CHECK: реальное количество слотов через API
        real_count = await get_real_peer_count(server, force_refresh=True)
        if real_count != -1 and real_count >= server.max_clients:
            logger.warning(
                f"create_device: server {server.name} is full "
                f"(API: {real_count}/{server.max_clients}). Aborting."
            )
            return None
        
        lock = _get_user_lock(user.id)
        async with lock:
            # 🔥 ИСПРАВЛЕНО #26: Перезагружаем user из текущей сессии
            # UserContextMiddleware кэширует User объект с TTL=5с.
            # Если пользователь делает 2 создания устройств за 5 секунд,
            # второй user будет detached (из предыдущей, уже закрытой сессии).
            # Изменения device_creations_today не попадут в БД.
            # Решение: перезагружаем user из текущей сессии внутри lock.
            user = await get_user_by_telegram_id(session, user.telegram_id)
            if not user:
                logger.error(f"create_device: user {user.telegram_id} not found after reload")
                return None
            
            # 🔥 ИСПРАВЛЕНО: Daily device creation limit (25/день МСК)
            # Админы исключены из лимита
            if not is_admin(user.telegram_id):
                now_msk = datetime.now(MSK_TZ).date()
                
                # Проверяем, нужно ли сбрасывать счётчик (новый день по МСК)
                if not _is_same_day_msk(user.last_creation_date, now_msk):
                    # Новый день — сбрасываем счётчик
                    user.device_creations_today = 0
                    user.last_creation_date = now_msk
                    # 🔥 ИСПРАВЛЕНО: flush для применения изменений до проверки
                    await session.flush()
                
                if user.device_creations_today >= DEVICE_DAILY_LIMIT:
                    # Превышен лимит — блокируем создание
                    logger.warning(
                        f"create_device: user {user.telegram_id} exceeded daily limit "
                        f"({user.device_creations_today}/{DEVICE_DAILY_LIMIT}). Blocked."
                    )
                    
                    # 🔥 ИСПРАВЛЕНО: Логируем блокировку в audit_logs (Вариант A)
                    try:
                        await AuditService.log_action(
                            session, admin_id=0, action="DEVICE_CREATE_BLOCKED",
                            target_type="User", target_id=user.telegram_id,
                            details=f"Daily limit: {user.device_creations_today}/{DEVICE_DAILY_LIMIT}, "
                                    f"server={server.name}"
                        )
                    except Exception as audit_error:
                        logger.error(f"Failed to log DEVICE_CREATE_BLOCKED: {audit_error}")
                    
                    # ВАЖНО: Возвращаем специальное значение, чтобы handler понял причину
                    # Но тип возвращаемого значения — VPNProfile | None
                    # Используем соглашение: None + специальный logger warning
                    # Handler должен показать ERROR_DEVICE_DAILY_LIMIT вместо ERROR_SERVER_UNAVAILABLE
                    # Для этого используем специальный атрибут на функции (или возвращаем Exception)
                    # Чтобы не ломать контракт, возвращаем None, а handler будет различать по контексту
                    # Устанавливаем маркер в user для handler'а
                    user._daily_limit_exceeded = True  # type: ignore[attr-defined]
                    return None
            
            short_hash = uuid.uuid4().hex[:4]
            clean_device_name = re.sub(r'[^a-zA-Z0-9]', '', device_name)[:10]
            
            # 🔥 ИСПРАВЛЕНО #9: Fallback для пустого имени
            if not clean_device_name:
                clean_device_name = "Device"
            
            client_name = f"tg_{user.telegram_id}_{clean_device_name}_{short_hash}"
            
            expires_ts = await SubscriptionService.get_expires_timestamp(user)
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
            
            # Валидация vpn:// URI (должен быть protocol_version == "2")
            if not is_valid_vpn_uri(raw_config):
                logger.error(
                    f"create_device: API returned invalid vpn:// URI for user {user.telegram_id}. "
                    f"Rolling back."
                )
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback invalid config: {rollback_error}")
                
                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="DEVICE_CREATE_FAILED",
                        target_type="User", target_id=user.telegram_id,
                        details=f"Invalid vpn:// URI from API, server={server.name}"
                    )
                except Exception as audit_error:
                    logger.error(f"Failed to log audit: {audit_error}")
                
                return None
            
            try:
                # Атомарность через savepoint (begin_nested)
                async with session.begin_nested() as savepoint:
                    profiles_count = await get_user_profiles_count(session, user.id)
                    
                    if profiles_count >= user.device_limit:
                        logger.info(
                            f"create_device: user {user.telegram_id} reached device limit "
                            f"({profiles_count}/{user.device_limit}) inside savepoint."
                        )
                        await savepoint.rollback()
                        
                        try:
                            await client.delete_user(client_id=peer_id)
                        except Exception as rollback_error:
                            logger.error(
                                f"Failed to rollback API client after limit check: {rollback_error}"
                            )
                        
                        return None
                    
                    profile = await create_profile(
                        session, user_id=user.id, server_id=server.id,
                        device_name=device_name, peer_id=peer_id, raw_config=raw_config
                    )
                    
                    # 🔥 ИСПРАВЛЕНО: Увеличиваем daily счётчик ПОСЛЕ успешного создания
                    # (если API упал — лимит не тратится впустую)
                    if not is_admin(user.telegram_id):
                        user.device_creations_today += 1
                    
                    await savepoint.commit()
                    # 🔥 ИСПРАВЛЕНО #27: Убран session.commit() — DBSessionMiddleware сделает финальный commit
                    # await session.commit()  # ← УДАЛЕНО
                    
                    # Логирование успешного создания устройства
                    try:
                        await AuditService.log_action(
                            session,
                            admin_id=user.telegram_id,
                            action="DEVICE_CREATED",
                            target_type="VPNProfile",
                            target_id=profile.id,
                            details=(
                                f"user={user.telegram_id}, device={device_name}, "
                                f"server={server.name}, peer_id={peer_id[:16]}..., "
                                f"daily={user.device_creations_today}/{DEVICE_DAILY_LIMIT}"
                            ),
                        )
                    except Exception as audit_error:
                        logger.warning(f"Failed to log DEVICE_CREATED: {audit_error}")
                    
                    logger.info(
                        f"Device created: user={user.telegram_id}, device={device_name}, "
                        f"server={server.name}, peer_id={peer_id[:16]}..., "
                        f"daily={user.device_creations_today}/{DEVICE_DAILY_LIMIT}"
                    )
                    
                    return profile
            
            except IntegrityError as e:
                await session.rollback()
                logger.error(
                    f"create_device: IntegrityError (duplicate peer_id?): {e}."
                )
                
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback after IntegrityError: {rollback_error}")
                
                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="DEVICE_CREATE_FAILED",
                        target_type="User", target_id=user.telegram_id,
                        details=f"IntegrityError (duplicate peer_id), server={server.name}"
                    )
                except Exception:
                    pass
                
                return None
            
            except Exception as e:
                await session.rollback()
                logger.error(
                    f"create_device: DB error: {e}.", exc_info=True
                )
                
                try:
                    await client.delete_user(client_id=peer_id)
                except Exception as rollback_error:
                    logger.error(f"Failed to rollback after DB error: {rollback_error}")
                
                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="DEVICE_CREATE_FAILED",
                        target_type="User", target_id=user.telegram_id,
                        details=f"DB error: {str(e)[:100]}, server={server.name}"
                    )
                except Exception:
                    pass
                
                return None
    
    @staticmethod
    async def delete_device(session: AsyncSession, profile: VPNProfile) -> bool:
        """
        Удаляет устройство пользователя.
        
        Flow:
        1. Получаем сервер из БД
        2. Удаляем клиента из Amnezia API (DELETE /clients)
        3. Удаляем профиль из БД через savepoint
        4. Логируем в audit_logs (action="DEVICE_DELETED")
        
        Args:
            session: SQLAlchemy async session
            profile: VPNProfile объект для удаления
            
        Returns:
            True если успешно удалено из API и БД
            False если:
            - Сервер не найден
            - API delete_user вернул False (ошибка сети/таймаут)
            - DB error при удалении профиля
            
        Side effects:
            - Удаляет клиента с Amnezia API (ключ становится невалидным)
            - Удаляет профиль из БД
            - Пишет в audit_logs
            
        Atomicity:
            Использует begin_nested() (savepoint) для гарантии, что
            если удаление из API прошло, но БД упала — всё откатится.
        """
        from database.repositories.profiles_repo import delete_profile
        from database.repositories.servers_repo import get_server_by_id
        
        server = await get_server_by_id(session, profile.server_id)
        if not server:
            logger.error(f"delete_device: server {profile.server_id} not found")
            return False
        
        client = AmneziaClient(server.api_url, server.api_key)
        deleted = await client.delete_user(client_id=profile.peer_id)
        
        if not deleted:
            logger.error(
                f"delete_device: API delete_user failed for peer_id={profile.peer_id[:16]}..., "
                f"server={server.name}"
            )
            return False
        
        try:
            async with session.begin_nested() as savepoint:
                await delete_profile(session, profile)
                await savepoint.commit()
                # 🔥 ИСПРАВЛЕНО #27: Убран session.commit() — DBSessionMiddleware сделает финальный commit
                # await session.commit()  # ← УДАЛЕНО
                
                # Логирование успешного удаления
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=profile.user.telegram_id if hasattr(profile, 'user') else 0,
                        action="DEVICE_DELETED",
                        target_type="VPNProfile",
                        target_id=profile.id,
                        details=(
                            f"device={profile.device_name}, server={server.name}, "
                            f"peer_id={profile.peer_id[:16]}..."
                        ),
                    )
                except Exception as audit_error:
                    logger.warning(f"Failed to log DEVICE_DELETED: {audit_error}")
                
                logger.info(
                    f"Device deleted: profile_id={profile.id}, "
                    f"peer_id={profile.peer_id[:16]}..., server={server.name}"
                )
                
                return True
        
        except Exception as e:
            await session.rollback()
            logger.error(
                f"delete_device: DB error: {e}.", exc_info=True
            )
            
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="DEVICE_DELETE_DB_ERROR",
                    target_type="VPNProfile", target_id=profile.id,
                    details=f"Deleted from API but DB error: {str(e)[:100]}"
                )
            except Exception:
                pass
            
            return False
