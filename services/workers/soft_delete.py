"""
Soft delete cleanup worker — удаление профилей soft-deleted пользователей из API.
🔥 ИСПРАВЛЕНО #5 (из Части 7): Background cleanup.
Проблема:
При soft delete пользователя (is_deleted=True) его VPN-профили остаются в БД,
а пиры на серверах Amnezia — навсегда. Это приводит к:
- Утечке ресурсов на серверах (пиры занимают слоты)
- Нарушению лимитов max_clients
- Потенциальной утечке данных (ключи остаются на серверах)
Решение:
Фоновый worker запускается каждые 24 часа:
1. Получает soft-deleted пользователей (старше 24ч)
2. Для каждого пользователя удаляет его профили из API
3. После успешного удаления из API — удаляет профили из БД
4. Логирует результат в audit_logs
Почему 24 часа задержки:
- Даёт время для восстановления (edge case: случайное удаление)
- Не блокирует критичные операции (создание/удаление устройств)
- Снижает нагрузку на API (batch deletion раз в сутки)
"""
import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete as sql_delete
from database.connection import get_session, session_scope
from database.models import VPNProfile, User
from database.repositories.users_repo import get_soft_deleted_users_with_profiles
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService
from bot.constants import CLEANUP_INTERVAL, WORKER_INITIAL_DELAY

logger = logging.getLogger("BackgroundWorker")

async def soft_delete_cleanup_loop(shutdown_event: asyncio.Event):
    """
    Фоновый worker очистки soft-deleted пользователей.
    🔥 ИСПРАВЛЕНО #5 (из Части 7): Graceful shutdown через shutdown_event.
    
    Логика:
    1. Initial delay 10 минут (даём боту запуститься)
    2. Каждые 24 часа (CLEANUP_INTERVAL):
       a. Получаем soft-deleted пользователей старше 24ч
       b. Для каждого пользователя удаляем профили из API
       c. После успешного удаления из API — удаляем профили из БД
       d. Логируем результат
    """
    # Initial delay с проверкой shutdown
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=WORKER_INITIAL_DELAY)
        logger.info("Soft delete cleanup stopped during initial delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass
    
    while not shutdown_event.is_set():
        try:
            session = await get_session()
            try:
                # Получаем soft-deleted пользователей с профилями (старше 24ч)
                users = await get_soft_deleted_users_with_profiles(session, older_than_hours=24)
                
                if not users:
                    logger.debug("Soft delete cleanup: no users to process")
                    continue
                
                logger.info(f"Soft delete cleanup: processing {len(users)} users")
                
                total_profiles_deleted = 0
                total_api_success = 0
                total_api_fail = 0
                
                for user in users:
                    if not user.profiles:
                        continue
                    
                    user_api_success = 0
                    user_api_fail = 0
                    profiles_to_delete_from_db = []
                    
                    # Группируем профили по серверам для batch deletion
                    profiles_by_server = {}
                    for profile in user.profiles:
                        if profile.server_id not in profiles_by_server:
                            profiles_by_server[profile.server_id] = []
                        profiles_by_server[profile.server_id].append(profile)
                    
                    # Удаляем профили из API для каждого сервера
                    for server_id, profiles in profiles_by_server.items():
                        # Получаем сервер для API credentials
                        server_stmt = select(User).where(User.id == user.id)
                        server_result = await session.execute(
                            select(
                                VPNProfile.server_id,
                                VPNProfile.peer_id,
                                VPNProfile.id
                            ).where(VPNProfile.id.in_([p.id for p in profiles]))
                        )
                        
                        # Получаем API credentials через relationship
                        first_profile = profiles[0]
                        server = first_profile.server
                        
                        if not server:
                            logger.warning(
                                f"Soft delete cleanup: server not found for profile {first_profile.id}, "
                                f"skipping API deletion"
                            )
                            user_api_fail += len(profiles)
                            continue
                        
                        client = AmneziaClient(server.api_url, server.api_key)
                        
                        for profile in profiles:
                            try:
                                # Удаляем из API
                                deleted = await client.delete_user(client_id=profile.peer_id)
                                if deleted:
                                    user_api_success += 1
                                    profiles_to_delete_from_db.append(profile.id)
                                else:
                                    user_api_fail += 1
                                    logger.warning(
                                        f"Soft delete cleanup: API delete failed for peer_id={profile.peer_id[:16]}..., "
                                        f"server={server.name}"
                                    )
                            except Exception as e:
                                user_api_fail += 1
                                logger.error(
                                    f"Soft delete cleanup: API delete error for peer_id={profile.peer_id[:16]}...: {e}"
                                )
                    
                    # Удаляем профили из БД (только те, что успешно удалены из API)
                    if profiles_to_delete_from_db:
                        try:
                            await session.execute(
                                sql_delete(VPNProfile).where(
                                    VPNProfile.id.in_(profiles_to_delete_from_db)
                                )
                            )
                            await session.flush()
                            logger.info(
                                f"Soft delete cleanup: deleted {len(profiles_to_delete_from_db)} profiles from DB "
                                f"for user {user.telegram_id}"
                            )
                        except Exception as e:
                            logger.error(
                                f"Soft delete cleanup: DB delete error for user {user.telegram_id}: {e}"
                            )
                    
                    # Логируем в audit
                    try:
                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="SOFT_DELETE_CLEANUP",
                            target_type="User",
                            target_id=user.telegram_id,
                            details=(
                                f"API: {user_api_success} success, {user_api_fail} fail, "
                                f"DB: {len(profiles_to_delete_from_db)} deleted"
                            ),
                        )
                    except Exception as e:
                        logger.warning(f"Soft delete cleanup: audit log failed: {e}")
                    
                    total_profiles_deleted += len(profiles_to_delete_from_db)
                    total_api_success += user_api_success
                    total_api_fail += user_api_fail
                
                # Commit всех изменений
                await session.commit()
                
                if total_profiles_deleted > 0 or total_api_fail > 0:
                    logger.info(
                        f"Soft delete cleanup completed: "
                        f"{total_profiles_deleted} profiles deleted from DB, "
                        f"{total_api_success} API success, {total_api_fail} API fail"
                    )
                
            finally:
                await session.close()
        
        except asyncio.CancelledError:
            logger.info("Soft delete cleanup cancelled")
            break
        except Exception as e:
            logger.error(f"Soft delete cleanup critical error: {e}", exc_info=True)
        
        # Ждём следующий цикл или shutdown
        if shutdown_event.is_set():
            break
        
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CLEANUP_INTERVAL)
            break
        except asyncio.TimeoutError:
            continue
    
    logger.info("Soft delete cleanup stopped gracefully")