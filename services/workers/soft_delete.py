"""
Soft delete cleanup worker.
🔥 ИСПРАВЛЕНО (Этап 2): Удалены неиспользуемые select-запросы (server_stmt, server_result).
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

                    profiles_by_server = {}
                    for profile in user.profiles:
                        if profile.server_id not in profiles_by_server:
                            profiles_by_server[profile.server_id] = []
                        profiles_by_server[profile.server_id].append(profile)

                    for server_id, profiles in profiles_by_server.items():
                        first_profile = profiles[0]
                        server = first_profile.server
                        
                        # 🔥 ИСПРАВЛЕНО (Этап 2): Удалены мусорные запросы server_stmt и server_result
                        # Раньше здесь были:
                        # server_stmt = select(User).where(User.id == user.id)
                        # server_result = await session.execute(...)
                        # которые никуда не сохранялись и не использовались.

                        if not server:
                            logger.warning(
                                f"Soft delete cleanup: server not found for profile {first_profile.id}"
                            )
                            user_api_fail += len(profiles)
                            continue

                        client = AmneziaClient(server.api_url, server.api_key)
                        for profile in profiles:
                            try:
                                deleted = await client.delete_user(client_id=profile.peer_id)
                                if deleted:
                                    user_api_success += 1
                                    profiles_to_delete_from_db.append(profile.id)
                                else:
                                    user_api_fail += 1
                            except Exception as e:
                                user_api_fail += 1
                                logger.error(f"Soft delete API error: {e}")

                    if profiles_to_delete_from_db:
                        try:
                            await session.execute(
                                sql_delete(VPNProfile).where(
                                    VPNProfile.id.in_(profiles_to_delete_from_db)
                                )
                            )
                            await session.flush()
                        except Exception as e:
                            logger.error(f"Soft delete DB error: {e}")

                    try:
                        await AuditService.log_action(
                            session, admin_id=0, action="SOFT_DELETE_CLEANUP",
                            target_type="User", target_id=user.telegram_id,
                            details=f"API: {user_api_success} success, {user_api_fail} fail, DB: {len(profiles_to_delete_from_db)} deleted",
                        )
                    except Exception as e:
                        logger.warning(f"Soft delete audit log failed: {e}")

                    total_profiles_deleted += len(profiles_to_delete_from_db)
                    total_api_success += user_api_success
                    total_api_fail += user_api_fail

                await session.commit()
                if total_profiles_deleted > 0 or total_api_fail > 0:
                    logger.info(
                        f"Soft delete cleanup completed: "
                        f"{total_profiles_deleted} DB, {total_api_success} API ok, {total_api_fail} API fail"
                    )
            finally:
                await session.close()

        except asyncio.CancelledError:
            logger.info("Soft delete cleanup cancelled")
            break
        except Exception as e:
            logger.error(f"Soft delete cleanup critical error: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=CLEANUP_INTERVAL)
                break
            except asyncio.TimeoutError:
                continue

    logger.info("Soft delete cleanup stopped gracefully")