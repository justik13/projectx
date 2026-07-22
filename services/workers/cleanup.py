import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import delete, select, update

from database.connection import session_scope
from database.models import (
    BroadcastProgress,
    PendingAPIDeletion,
    User,
    VPNProfile,
)
from database.repositories.audit_repo import clear_audit_logs
from database.repositories.servers_repo import get_active_servers
from services.amnezia_client import AmneziaClient
from services.profile_deletion_service import ProfileDeletionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600

# Короткая стартовая задержка вместо 10 минут.
CLEANUP_START_DELAY = 60.0

# Основной цикл очистки запускаем чаще, чтобы grace-удаление
# и pending API deletions обрабатывались своевременно.
CLEANUP_LOOP_INTERVAL = 900.0

# Grace-период после истечения подписки.
# Через 48 часов устройства удаляются полностью.
GRACE_PERIOD_HOURS = 48

# Старые broadcast/audit логи чистим не чаще раза в сутки.
OLD_RECORDS_INTERVAL = 86400.0

_last_old_cleanup: float = 0.0


async def cleanup_dangling_peers_loop(shutdown_event: asyncio.Event):
    try:
        await asyncio.wait_for(
            shutdown_event.wait(),
            timeout=CLEANUP_START_DELAY,
        )
        logger.info("Cleanup worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            await _cleanup_expired_profiles_grace()
            await _cleanup_dangling_peers()
            await _process_pending_deletions()

            now = time.monotonic()
            if now - _last_old_cleanup > OLD_RECORDS_INTERVAL:
                await _cleanup_old_records()
                _last_old_cleanup = now

        except asyncio.CancelledError:
            logger.info("Cleanup worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Критическая ошибка в цикле очистки: %s",
                e,
                exc_info=True,
            )

            if shutdown_event.is_set():
                break

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=CLEANUP_LOOP_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Cleanup worker stopped gracefully")


async def _cleanup_expired_profiles_grace():
    """
    Удаляет устройства пользователей, у которых подписка истекла
    более чем на 48 часов.

    Правила:
    - если подписка истекла меньше 48 часов назад, устройства ещё живут;
    - если пользователь продлил подписку, удаление не происходит;
    - вечные подписки не удаляются;
    - удалённые пользователи не обрабатываются;
    - удаление с сервера выполняется через ProfileDeletionService.
    """
    current_time = now_utc()
    threshold = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.is_deleted == False,
                User.subscription_end != None,
                User.subscription_end < threshold,
            )
            .order_by(User.subscription_end.asc())
            .limit(50)
        )
        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    deleted_users_count = 0
    deleted_profiles_count = 0

    for user_id in user_ids:
        try:
            async with session_scope() as session:
                user_stmt = (
                    select(User)
                    .where(User.id == user_id)
                    .with_for_update()
                )
                user_result = await session.execute(user_stmt)
                user = user_result.scalar_one_or_none()

                if user is None:
                    continue

                if user.is_deleted:
                    continue

                if user.subscription_end is None:
                    continue

                # Вечная подписка.
                if user.subscription_end.year >= 2100:
                    continue

                # Если пользователь уже продлил подписку,
                # ничего не удаляем.
                if user.subscription_end >= threshold:
                    continue

                profiles_stmt = select(VPNProfile).where(
                    VPNProfile.user_id == user.id,
                )
                profiles_result = await session.execute(profiles_stmt)
                profiles = list(profiles_result.scalars().all())

                if not profiles:
                    continue

                deleted = await ProfileDeletionService.delete_profiles_list(
                    session,
                    profiles,
                    reason="grace_delete",
                    background=True,
                )

                if deleted > 0:
                    deleted_users_count += 1
                    deleted_profiles_count += deleted

                    logger.info(
                        "Grace cleanup: removed %s expired profiles "
                        "for user_id=%s (subscription_end=%s)",
                        deleted,
                        user_id,
                        user.subscription_end,
                    )

        except Exception as e:
            logger.error(
                "Grace cleanup failed for user_id=%s: %s",
                user_id,
                e,
                exc_info=True,
            )

    if deleted_users_count > 0:
        logger.info(
            "Grace cleanup completed: %s users, %s profiles removed",
            deleted_users_count,
            deleted_profiles_count,
        )


async def _queue_zombie_deletion(
    server_info: dict,
    peer_id: str,
    client_name: str | None,
    error_text: str,
) -> None:
    """
    Ставит зомби-пира в очередь pending_api_deletions,
    если API недоступен или удаление не удалось.

    Дальше pending_api_deletions обрабатывается общим механизмом:
    - повторные попытки;
    - лимит 10 попыток;
    - алерт админам после исчерпания попыток.
    """
    try:
        async with session_scope() as session:
            pending = PendingAPIDeletion(
                server_name=server_info["name"],
                api_url=server_info["api_url"],
                api_key=server_info["api_key"],
                peer_id=peer_id,
                client_name=client_name or f"zombie_{peer_id[:16]}",
                reason="zombie_peer_cleanup_failed",
                attempts=1,
                last_attempt_at=now_utc(),
                last_error=error_text,
            )
            session.add(pending)
            await session.flush()

            logger.warning(
                "Queued zombie peer for pending deletion: "
                "server=%s, peer=%s..., reason=zombie_peer_cleanup_failed",
                server_info["name"],
                peer_id[:16],
            )

    except Exception as e:
        logger.error(
            "Failed to queue zombie peer deletion: server=%s, peer=%s..., error=%s",
            server_info["name"],
            peer_id[:16],
            e,
            exc_info=True,
        )


async def _cleanup_dangling_peers():
    servers_data = []
    db_peer_ids = set()

    async with session_scope() as session:
        servers = await get_active_servers(session)

        result = await session.execute(select(VPNProfile.peer_id))
        db_peer_ids = {row[0] for row in result.all() if row[0]}

        servers_data = [
            {
                "api_url": s.api_url,
                "api_key": s.api_key,
                "name": s.name,
                "id": s.id,
            }
            for s in servers
        ]

    if not servers_data:
        return

    async def _fetch_api_peers(server_info):
        client = AmneziaClient(
            server_info["api_url"],
            server_info["api_key"],
        )

        try:
            api_clients_list = await client.get_all_clients()

            if api_clients_list is None:
                return server_info, []

            return server_info, api_clients_list

        except Exception as e:
            logger.error(
                "Ошибка получения списка пиров на %s: %s",
                server_info["name"],
                e,
            )
            return server_info, []

    tasks = [_fetch_api_peers(s) for s in servers_data]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, Exception):
            continue

        server_info, api_clients_list = result

        if not api_clients_list:
            continue

        for api_client in api_clients_list:
            client_id = api_client.id
            client_name = api_client.clientName or api_client.name

            if not client_name.startswith("tg_"):
                continue

            if client_id in db_peer_ids:
                continue

            peer_exists_in_db = False

            try:
                async with session_scope() as session:
                    fresh_result = await session.execute(
                        select(VPNProfile.id).where(
                            VPNProfile.peer_id == client_id
                        )
                    )
                    peer_exists_in_db = fresh_result.first() is not None

            except Exception as e:
                logger.error(
                    "Double-check failed for peer %s...: %s",
                    client_id[:16],
                    e,
                )
                continue

            if peer_exists_in_db:
                continue

            # Если по этому пиру уже есть pending-запись,
            # не создаём дубликат. Его обработает _process_pending_deletions.
            try:
                async with session_scope() as session:
                    existing_pending = await session.scalar(
                        select(PendingAPIDeletion.id)
                        .where(PendingAPIDeletion.peer_id == client_id)
                        .limit(1)
                    )

                    if existing_pending:
                        continue

            except Exception as e:
                logger.error(
                    "Failed to check existing pending deletion "
                    "for zombie peer %s...: %s",
                    client_id[:16],
                    e,
                )
                continue

            logger.warning(
                "Удаляю 'призрака' %s на %s",
                client_name,
                server_info["name"],
            )

            deleted = False
            error_text = None

            try:
                client = AmneziaClient(
                    server_info["api_url"],
                    server_info["api_key"],
                )
                deleted = await client.delete_user(client_id=client_id)

            except Exception as e:
                error_text = f"{type(e).__name__}: {str(e)[:200]}"

            if deleted:
                logger.info(
                    "Зомби-пир удалён: server=%s, peer=%s...",
                    server_info["name"],
                    client_id[:16],
                )
            else:
                logger.warning(
                    "Не удалось удалить зомби-пира: server=%s, peer=%s..., "
                    "ставлю в pending queue",
                    server_info["name"],
                    client_id[:16],
                )

                await _queue_zombie_deletion(
                    server_info=server_info,
                    peer_id=client_id,
                    client_name=client_name,
                    error_text=(
                        error_text
                        or "API delete_user returned False"
                    ),
                )


async def _process_pending_deletions():
    pending_deletions_data = []

    async with session_scope() as session:
        current_time = now_utc()

        stmt = (
            select(PendingAPIDeletion)
            .order_by(PendingAPIDeletion.created_at)
            .limit(200)
        )
        result = await session.execute(stmt)
        pending_deletions = result.scalars().all()

        for deletion in pending_deletions:
            if deletion.attempts >= MAX_PENDING_ATTEMPTS:
                pending_deletions_data.append(
                    {
                        "id": deletion.id,
                        "expired": True,
                        "attempts": deletion.attempts,
                        "peer_id": deletion.peer_id,
                        "server_name": deletion.server_name,
                        "reason": deletion.reason,
                    }
                )
                continue

            if deletion.last_attempt_at:
                time_since_last = (
                    current_time - deletion.last_attempt_at
                ).total_seconds()

                if time_since_last < PENDING_RETRY_INTERVAL:
                    continue

            pending_deletions_data.append(
                {
                    "id": deletion.id,
                    "expired": False,
                    "attempts": deletion.attempts,
                    "api_url": deletion.api_url,
                    "api_key": deletion.api_key,
                    "peer_id": deletion.peer_id,
                    "server_name": deletion.server_name,
                    "reason": deletion.reason,
                }
            )

    if not pending_deletions_data:
        return

    logger.info(
        "Processing %s pending API deletions",
        len(pending_deletions_data),
    )

    success_count = 0
    fail_count = 0
    expired_count = 0
    expired_ids = []

    for deletion_data in pending_deletions_data:
        deletion_id = deletion_data["id"]

        if deletion_data.get("expired"):
            expired_count += 1
            expired_ids.append(deletion_id)

            logger.warning(
                "Pending deletion expired after %s attempts: "
                "server=%s, peer=%s..., reason=%s",
                deletion_data["attempts"],
                deletion_data["server_name"],
                deletion_data["peer_id"][:16],
                deletion_data.get("reason") or "unknown",
            )
            continue

        attempts = deletion_data["attempts"]
        peer_id = deletion_data["peer_id"]
        server_name = deletion_data["server_name"]
        reason = deletion_data.get("reason") or "unknown"

        client = AmneziaClient(
            deletion_data["api_url"],
            deletion_data["api_key"],
        )

        deleted = False
        error_text = None

        try:
            deleted = await client.delete_user(client_id=peer_id)
        except Exception as e:
            error_text = f"{type(e).__name__}: {str(e)[:200]}"

        async with session_scope() as session:
            current_time = now_utc()

            if deleted:
                await session.execute(
                    delete(PendingAPIDeletion).where(
                        PendingAPIDeletion.id == deletion_id
                    )
                )

                success_count += 1

                logger.info(
                    "Pending peer deleted: server=%s, peer=%s... "
                    "(attempt %s, reason=%s)",
                    server_name,
                    peer_id[:16],
                    attempts + 1,
                    reason,
                )

            else:
                await session.execute(
                    update(PendingAPIDeletion)
                    .where(PendingAPIDeletion.id == deletion_id)
                    .values(
                        attempts=attempts + 1,
                        last_attempt_at=current_time,
                        last_error=(
                            error_text
                            or "API delete_user returned False"
                        ),
                    )
                )

                fail_count += 1

    if expired_ids:
        async with session_scope() as session:
            await session.execute(
                delete(PendingAPIDeletion).where(
                    PendingAPIDeletion.id.in_(expired_ids)
                )
            )

    if success_count > 0 or fail_count > 0 or expired_count > 0:
        logger.info(
            "Pending deletions processed: %s success, %s fail, "
            "%s expired",
            success_count,
            fail_count,
            expired_count,
        )

    if expired_count > 0:
        try:
            from services.workers.heartbeat import get_bot_ref
            from config.settings import get_settings

            bot = get_bot_ref()

            if bot:
                settings = get_settings()

                alert_msg = (
                    "🚨 <b>Не удалось удалить устройства на сервере</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>{expired_count}</b> записей достигли лимита попыток.\n"
                    "Они удалены из очереди, но могли остаться на сервере.\n"
                    "<i>Требуется ручная проверка.</i>"
                )

                for admin_id in settings.ADMIN_IDS:
                    try:
                        await bot.send_message(
                            admin_id,
                            alert_msg,
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

        except Exception as alert_error:
            logger.error(
                "Failed to send pending deletion alert: %s",
                alert_error,
            )


async def _cleanup_old_records():
    async with session_scope() as session:
        current_time = now_utc()

        threshold_broadcasts = current_time - timedelta(days=7)

        stmt_broadcasts = (
            delete(BroadcastProgress)
            .where(
                BroadcastProgress.status.in_(
                    ["completed", "stopped"]
                )
            )
            .where(BroadcastProgress.updated_at < threshold_broadcasts)
        )
        result_broadcasts = await session.execute(stmt_broadcasts)
        broadcasts_deleted = result_broadcasts.rowcount

        deleted_logs = await clear_audit_logs(
            session,
            older_than_days=30,
        )

        if broadcasts_deleted > 0 or deleted_logs > 0:
            logger.info(
                "Cleanup: %s old broadcasts, %s old audit logs deleted",
                broadcasts_deleted,
                deleted_logs,
            )