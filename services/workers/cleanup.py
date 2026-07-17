import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, delete, update
from database.connection import session_scope
from database.repositories.servers_repo import get_active_servers
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, PendingAPIDeletion
from bot.constants import CLEANUP_INTERVAL, WORKER_INITIAL_DELAY

logger = logging.getLogger("BackgroundWorker")

MAX_PENDING_ATTEMPTS = 10
PENDING_RETRY_INTERVAL = 3600  # Повторять попытки раз в час


async def cleanup_dangling_peers_loop(shutdown_event: asyncio.Event):
    """
    Воркер очистки dangling пиров + обработки zombie-пиров.
    
    Две задачи:
    1. Поиск пиров в API, которых нет в БД (dangling) — удаление призраков
    2. Обработка pending_api_deletions — пиры, которые не удалось удалить
       при удалении сервера (zombie-пиры)
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
    
    Логика:
    1. Выбираем записи с attempts < MAX_PENDING_ATTEMPTS
    2. Для каждой пытаемся удалить пир из API
    3. При успехе — удаляем запись из таблицы
    4. При неудаче — увеличиваем attempts, логируем ошибку
    5. Если attempts >= MAX — удаляем запись (чтобы не спамить)
    """
    async with session_scope() as session:
        # Получаем pending-записи для обработки
        stmt = (
            select(PendingAPIDeletion)
            .where(PendingAPIDeletion.attempts < MAX_PENDING_ATTEMPTS)
            .order_by(PendingAPIDeletion.created_at)
            .limit(50)  # Обрабатываем максимум 50 за запуск
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
        # Проверяем, не пора ли удалить запись (слишком много попыток)
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

        # Проверяем, прошло ли достаточно времени с последней попытки
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if deletion.last_attempt_at:
            time_since_last = (now - deletion.last_attempt_at).total_seconds()
            if time_since_last < PENDING_RETRY_INTERVAL:
                continue

        # Пытаемся удалить пир из API
        client = AmneziaClient(deletion.api_url, deletion.api_key)
        try:
            deleted = await client.delete_user(client_id=deletion.peer_id)
            
            async with session_scope() as session:
                if deleted:
                    # Успех — удаляем запись
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
                    # Неудача — увеличиваем счётчик
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
            # Исключение — логируем и увеличиваем счётчик
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