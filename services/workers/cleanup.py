import asyncio
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete, update
from database.connection import session_scope
from database.repositories.servers_repo import get_active_servers
from database.repositories.audit_repo import clear_audit_logs
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, PendingAPIDeletion, BroadcastProgress, AuditLog
from bot.constants import CLEANUP_INTERVAL, WORKER_INITIAL_DELAY

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
            # ═══════════════════════════════════════════════════════════
            # 🧹 ЗАДАЧА 1: Очистка dangling пиров (призраков)
            # ═══════════════════════════════════════════════════════════
            await _cleanup_dangling_peers()

            # ═══════════════════════════════════════════════════════════
            # 🧟 ЗАДАЧА 2: Обработка zombie-пиров (pending_api_deletions)
            # ═══════════════════════════════════════════════════════════
            await _process_pending_deletions()

            # ═══════════════════════════════════════════════════════════
            # 🔥 MUST FIX #10: Очистка старых записей
            # ═══════════════════════════════════════════════════════════
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

    async def _clean_server_dangling_peers(server_info, db_peer_ids_set):
        client = AmneziaClient(server_info['api_url'], server_info['api_key'])
        try:
            api_clients_list = await client.get_all_clients()
            if api_clients_list is None:
                return

            for api_client in api_clients_list:
                client_id = api_client.id
                client_name = api_client.clientName or api_client.name

                # Обрабатываем только наших пиров (префикс tg_)
                if not client_name.startswith("tg_"):
                    continue
                if client_id in db_peer_ids_set:
                    continue

                # Double-check: проверяем в свежей сессии
                async with session_scope() as session2:
                    fresh_result = await session2.execute(
                        select(VPNProfile.id).where(VPNProfile.peer_id == client_id)
                    )
                    if fresh_result.first():
                        continue

                logger.warning(
                    f"Удаляю 'призрака' {client_name} на {server_info['name']}"
                )
                await client.delete_user(client_id=client_id)
        except Exception as e:
            logger.error(
                f"Ошибка очистки призраков на {server_info['name']}: {e}"
            )

    tasks = [
        _clean_server_dangling_peers(s, db_peer_ids)
        for s in servers_data
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process_pending_deletions():
    """
    🔥 НОВОЕ: Обрабатывает zombie-пиры из таблицы pending_api_deletions.
    Когда сервер удаляется из БД, но API недоступен — записи остаются
    в pending_api_deletions. Этот воркер периодически пытается их удалить.
    """
    async with session_scope() as session:
        stmt = (
            select(PendingAPIDeletion)
            .where(PendingAPIDeletion.attempts < MAX_PENDING_ATTEMPTS)
            .order_by(PendingAPIDeletion.created_at)
            .limit(50)
        )
        result = await session.execute(stmt)
        pending_deletions = result.scalars().all()

    if not pending_deletions:
        return

    logger.info(
        f"Processing {len(pending_deletions)} pending API deletions "
        f"(zombie peers)"
    )

    success_count = 0
    fail_count = 0
    expired_count = 0

    for deletion in pending_deletions:
        if deletion.attempts >= MAX_PENDING_ATTEMPTS:
            expired_count += 1
            async with session_scope() as session:
                await session.execute(
                    delete(PendingAPIDeletion).where(
                        PendingAPIDeletion.id == deletion.id
                    )
                )
            logger.warning(
                f"Pending deletion expired after {deletion.attempts} attempts: "
                f"server={deletion.server_name}, peer={deletion.peer_id[:16]}..."
            )
            continue

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if deletion.last_attempt_at:
            time_since_last = (now - deletion.last_attempt_at).total_seconds()
            if time_since_last < PENDING_RETRY_INTERVAL:
                continue

        client = AmneziaClient(deletion.api_url, deletion.api_key)
        try:
            deleted = await client.delete_user(client_id=deletion.peer_id)
            async with session_scope() as session:
                if deleted:
                    await session.execute(
                        delete(PendingAPIDeletion).where(
                            PendingAPIDeletion.id == deletion.id
                        )
                    )
                    success_count += 1
                    logger.info(
                        f"Zombie peer deleted: server={deletion.server_name}, "
                        f"peer={deletion.peer_id[:16]}... "
                        f"(attempt {deletion.attempts + 1})"
                    )
                else:
                    await session.execute(
                        update(PendingAPIDeletion)
                        .where(PendingAPIDeletion.id == deletion.id)
                        .values(
                            attempts=deletion.attempts + 1,
                            last_attempt_at=now,
                            last_error="API delete_user returned False",
                        )
                    )
                    fail_count += 1
        except Exception as e:
            async with session_scope() as session:
                await session.execute(
                    update(PendingAPIDeletion)
                    .where(PendingAPIDeletion.id == deletion.id)
                    .values(
                        attempts=deletion.attempts + 1,
                        last_attempt_at=now,
                        last_error=f"{type(e).__name__}: {str(e)[:200]}",
                    )
                )
                fail_count += 1
            logger.warning(
                f"Failed to delete zombie peer: "
                f"server={deletion.server_name}, "
                f"peer={deletion.peer_id[:16]}..., "
                f"error={type(e).__name__}"
            )

    if success_count > 0 or fail_count > 0 or expired_count > 0:
        logger.info(
            f"Pending deletions processed: "
            f"{success_count} success, {fail_count} fail, "
            f"{expired_count} expired"
        )

    # 🔥 СКРЫТАЯ УЯЗВИМОСТЬ #14: Алерт админам при достижении лимита попыток
    # Если API сервера хронически недоступен, зомби-пиры будут удаляться из БД,
    # но навсегда останутся на сервере Amnezia, занимая слоты.
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
                    f"Они удалены из БД, но <b>остались на серверах Amnezia</b>.\n\n"
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
        # Очистка завершённых BroadcastProgress старше 7 дней
        threshold_broadcasts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
        stmt_broadcasts = (
            delete(BroadcastProgress)
            .where(BroadcastProgress.status.in_(["completed", "stopped"]))
            .where(BroadcastProgress.updated_at < threshold_broadcasts)
        )
        result_broadcasts = await session.execute(stmt_broadcasts)
        broadcasts_deleted = result_broadcasts.rowcount

        # Очистка AuditLog старше 30 дней
        deleted_logs = await clear_audit_logs(session, older_than_days=30)

        if broadcasts_deleted > 0 or deleted_logs > 0:
            logger.info(
                f"Cleanup: {broadcasts_deleted} old broadcasts, "
                f"{deleted_logs} old audit logs deleted"
            )