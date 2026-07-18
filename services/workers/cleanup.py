import asyncio
import logging
from datetime import timedelta
from sqlalchemy import select, delete, update
from database.connection import session_scope
from database.repositories.servers_repo import get_active_servers
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, PendingAPIDeletion, BroadcastProgress, AuditLog
from bot.constants import CLEANUP_INTERVAL, WORKER_INITIAL_DELAY
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")
MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600  # Повторять попытки раз в час


async def cleanup_dangling_peers_loop(shutdown_event: asyncio.Event):
    """
    Воркер очистки dangling пиров + обработки zombie-пиров.
    Три задачи:
    1. Поиск пиров в API, которых нет в БД (dangling) — удаление призраков
    2. Обработка pending_api_deletions — пиры, которые не удалось удалить
       при удалении сервера (zombie-пиры)
    3. 🔥 MUST FIX #10: Очистка старых BroadcastProgress и AuditLog
    """
    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=WORKER_INITIAL_DELAY)
        logger.info("Cleanup worker stopped during initial delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            await _cleanup_dangling_peers()
            await _process_pending_deletions()
            await _cleanup_old_records()
        except asyncio.CancelledError:
            logger.info("Cleanup worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле очистки: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=CLEANUP_INTERVAL)
                break
            except asyncio.TimeoutError:
                continue

    logger.info("Cleanup worker stopped gracefully")
async def _cleanup_dangling_peers():
    """
    Ищет в API Amnezia пиры с префиксом 'tg_', которых нет в БД.
    Это "призраки" — созданные, но не сохранённые в БД (из-за сбоя),
    или удалённые из БД, но оставшиеся на сервере.
    """
    servers_data = []
    db_peer_ids = set()

    async with session_scope() as session:
        servers = await get_active_servers(session)
        result = await session.execute(select(VPNProfile.peer_id))
        db_peer_ids = {row[0] for row in result.all()}
        servers_data = [
            {
                'api_url': s.api_url,
                'api_key': s.api_key,
                'name': s.name,
                'id': s.id,
            }
            for s in servers
        ]

    if not db_peer_ids or all(p is None for p in db_peer_ids):
        return
    async def _fetch_api_peers(server_info):
        client = AmneziaClient(server_info['api_url'], server_info['api_key'])
        try:
            api_clients_list = await client.get_all_clients()
            if api_clients_list is None:
                return server_info, []
            return server_info, api_clients_list
        except Exception as e:
            logger.error(
                f"Ошибка получения списка пиров на {server_info['name']}: {e}"
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
                        select(VPNProfile.id).where(VPNProfile.peer_id == client_id)
                    )
                    peer_exists_in_db = fresh_result.first() is not None
            except Exception as e:
                logger.error(
                    f"Double-check failed for peer {client_id[:16]}...: {e}"
                )
                continue

            if peer_exists_in_db:
                continue

            logger.warning(
                f"Удаляю 'призрака' {client_name} на {server_info['name']}"
            )
            try:
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                await client.delete_user(client_id=client_id)
            except Exception as e:
                logger.error(
                    f"Ошибка удаления призрака {client_id[:16]}... на "
                    f"{server_info['name']}: {e}"
                )
async def _process_pending_deletions():
    """
    🔥 НОВОЕ: Обрабатывает zombie-пиры из таблицы pending_api_deletions.
    Когда сервер удаляется из БД, но API недоступен — записи остаются
    в pending_api_deletions. Этот воркер периодически пытается их удалить.
    """
    pending_deletions_data = []
    async with session_scope() as session:
        current_time = now_utc()
        stmt = (
            select(PendingAPIDeletion)
            .where(PendingAPIDeletion.attempts < MAX_PENDING_ATTEMPTS)
            .order_by(PendingAPIDeletion.created_at)
            .limit(50)
        )
        result = await session.execute(stmt)
        pending_deletions = result.scalars().all()
        for deletion in pending_deletions:
            if deletion.last_attempt_at:
                time_since_last = (
                    current_time - deletion.last_attempt_at
                ).total_seconds()
                if time_since_last < PENDING_RETRY_INTERVAL:
                    continue

            pending_deletions_data.append({
                'id': deletion.id,
                'attempts': deletion.attempts,
                'api_url': deletion.api_url,
                'api_key': deletion.api_key,
                'peer_id': deletion.peer_id,
                'server_name': deletion.server_name,
            })

    if not pending_deletions_data:
        return

    logger.info(
        f"Processing {len(pending_deletions_data)} pending API deletions "
        f"(zombie peers)"
    )

    success_count = 0
    fail_count = 0
    expired_count = 0
    for deletion_data in pending_deletions_data:
        deletion_id = deletion_data['id']
        attempts = deletion_data['attempts']
        peer_id = deletion_data['peer_id']
        server_name = deletion_data['server_name']

        if attempts >= MAX_PENDING_ATTEMPTS:
            expired_count += 1
            async with session_scope() as session:
                await session.execute(
                    delete(PendingAPIDeletion).where(
                        PendingAPIDeletion.id == deletion_id
                    )
                )
            logger.warning(
                f"Pending deletion expired after {attempts} attempts: "
                f"server={server_name}, peer={peer_id[:16]}..."
            )
            continue
        client = AmneziaClient(deletion_data['api_url'], deletion_data['api_key'])
        try:
            deleted = await client.delete_user(client_id=peer_id)
        except Exception as e:
            deleted = False
            async with session_scope() as session:
                current_time = now_utc()
                await session.execute(
                    update(PendingAPIDeletion)
                    .where(PendingAPIDeletion.id == deletion_id)
                    .values(
                        attempts=attempts + 1,
                        last_attempt_at=current_time,
                        last_error=f"{type(e).__name__}: {str(e)[:200]}",
                    )
                )
            fail_count += 1
            logger.warning(
                f"Failed to delete zombie peer: "
                f"server={server_name}, "
                f"peer={peer_id[:16]}..., "
                f"error={type(e).__name__}"
            )
            continue
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
                    f"Zombie peer deleted: server={server_name}, "
                    f"peer={peer_id[:16]}... "
                    f"(attempt {attempts + 1})"
                )
            else:
                await session.execute(
                    update(PendingAPIDeletion)
                    .where(PendingAPIDeletion.id == deletion_id)
                    .values(
                        attempts=attempts + 1,
                        last_attempt_at=current_time,
                        last_error="API delete_user returned False",
                    )
                )
                fail_count += 1

    if success_count > 0 or fail_count > 0 or expired_count > 0:
        logger.info(
            f"Pending deletions processed: "
            f"{success_count} success, {fail_count} fail, "
            f"{expired_count} expired"
        )
    if expired_count > 0:
        try:
            from services.workers.heartbeat import get_bot_ref
            from config.settings import get_settings
            bot = get_bot_ref()
            if bot:
                settings = get_settings()
                alert_msg = (
                    f"🚨 <b>Зомби-пиры не удалились!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>{expired_count}</b> записей достигли лимита попыток (10).\n"
                    f"Они удалены из БД, но <b>остались на серверах Amnezia</b>.\n"
                    f"<i>Требуется ручная очистка серверов через Amnezia API или админ-панель сервера.</i>"
                )
                for admin_id in settings.ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, alert_msg, parse_mode="HTML")
                    except Exception:
                        pass
        except Exception as alert_error:
            logger.error(f"Failed to send zombie peers alert: {alert_error}")


async def _cleanup_old_records():
    """
    🔥 MUST FIX #10: Очистка старых записей из BroadcastProgress и AuditLog.
    Предотвращает бесконечный рост таблиц.
    """
    async with session_scope() as session:
        current_time = now_utc()
        threshold_broadcasts = current_time - timedelta(days=7)
        stmt_broadcasts = (
            delete(BroadcastProgress)
            .where(BroadcastProgress.status.in_(["completed", "stopped"]))
            .where(BroadcastProgress.updated_at < threshold_broadcasts)
        )
        result_broadcasts = await session.execute(stmt_broadcasts)
        broadcasts_deleted = result_broadcasts.rowcount
        deleted_logs = await clear_audit_logs(session, older_than_days=30)

        if broadcasts_deleted > 0 or deleted_logs > 0:
            logger.info(
                f"Cleanup: {broadcasts_deleted} old broadcasts, "
                f"{deleted_logs} old audit logs deleted"
            )