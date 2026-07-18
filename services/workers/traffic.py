import asyncio
import logging
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy import select, update, case
from database.connection import session_scope
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, Server, User
from bot.constants import (
    TRAFFIC_SYNC_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL,
    SELF_HEALING_MAX_PER_CYCLE,
)

logger = logging.getLogger("BackgroundWorker")
BATCH_SIZE = 100

async def traffic_sync_loop(shutdown_event: asyncio.Event):
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=TRAFFIC_SYNC_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                pass

            # 🔥 ИСПРАВЛЕНО: Получаем только список активных серверов (лёгкий запрос)
            async with session_scope() as session:
                stmt = select(Server.id, Server.api_url, Server.api_key, Server.name, Server.is_active)
                result = await session.execute(stmt)
                servers = [
                    {
                        'id': row[0], 'api_url': row[1], 'api_key': row[2],
                        'name': row[3], 'is_active': row[4]
                    }
                    for row in result.all()
                ]

            if not servers:
                continue

            # Получаем трафик со всех серверов параллельно
            async def _fetch_server_traffic(server_info):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                try:
                    api_clients_list = await client.get_all_clients()
                    if api_clients_list is None:
                        return server_info['id'], None
                    return server_info['id'], {c.id: c for c in api_clients_list}
                except Exception as e:
                    logger.error(f"Ошибка трафика с {server_info['name']}: {e}")
                    return server_info['id'], None

            tasks = [_fetch_server_traffic(s) for s in servers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            api_data_by_server = {
                r[0]: r[1]
                for r in results
                if not isinstance(r, Exception) and r is not None and r[1] is not None
            }

            # 🔥 ИСПРАВЛЕНО: Обрабатываем каждый сервер ОТДЕЛЬНО батчами
            for server_info in servers:
                server_id = server_info['id']
                if server_id not in api_data_by_server:
                    continue
                api_clients = api_data_by_server[server_id]
                await _process_server_traffic(server_info, api_clients)

        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле трафика: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

    logger.info("Traffic sync worker stopped gracefully")


async def _process_server_traffic(server_info, api_clients):
    """
    🔥 ИСПРАВЛЕНО (MUST FIX #1): Reverse Self-Healing
    БД — единственный источник истины для is_active.
    Если в БД is_active=True, а в API disabled — чиним API, а не БД.
    """
    server_id = server_info['id']
    server_is_active = server_info['is_active']

    # Загружаем профили этого сервера батчами через stream()
    async with session_scope() as session:
        stmt = (
            select(
                VPNProfile.id, VPNProfile.peer_id,
                VPNProfile.traffic_down, VPNProfile.traffic_up,
                VPNProfile.last_connected, VPNProfile.is_active,
                User.is_banned, User.telegram_id, User.subscription_end,
            )
            .join(User, VPNProfile.user_id == User.id)
            .where(VPNProfile.server_id == server_id)
        )
        result = await session.stream(stmt)

        updates_data = {}
        healing_tasks = []
        reverse_healing_tasks = []  # 🔥 НОВОЕ: чиним API когда БД говорит active
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        # 🔥 ИСПРАВЛЕНО: Обрабатываем по одной строке, не загружая всё в RAM
        async for row in result.partitions(size=BATCH_SIZE):
            for (
                p_id, peer_id, t_down, t_up, last_conn, is_active,
                is_banned, tg_id, sub_end,
            ) in row:
                if peer_id not in api_clients:
                    continue

                api_data = api_clients[peer_id]
                api_t_down = api_data.traffics.totalDownload
                api_t_up = api_data.traffics.totalUpload

                new_t_down = api_t_down if api_t_down is not None else t_down
                new_t_up = api_t_up if api_t_up is not None else t_up

                last_conn_raw = api_data.lastHandshake or api_data.lastSeen or api_data.updatedAt
                new_last_connected = last_conn
                if last_conn_raw:
                    try:
                        ts = int(float(str(last_conn_raw)))
                        if ts > 1e12:
                            ts = ts // 1000
                        new_last_connected = datetime.fromtimestamp(
                            ts, tz=timezone.utc
                        ).replace(tzinfo=None)
                    except (ValueError, TypeError, OverflowError):
                        pass

                api_is_active = (api_data.status == "active")
                is_subscription_expired = sub_end and sub_end < now_utc
                local_should_be_disabled = (
                    (not is_active) or is_banned or is_subscription_expired
                )

                # ═══════════════════════════════════════════════════════════
                # 🔥 MUST FIX #1: Self-Healing (оба направления)
                # ═══════════════════════════════════════════════════════════

                # Направление 1: БД говорит "отключить" → API active → отключаем API
                if local_should_be_disabled and api_is_active:
                    if not server_is_active:
                        # Сервер выключен — только обновляем трафик
                        if (t_down != new_t_down or t_up != new_t_up or last_conn != new_last_connected):
                            updates_data[p_id] = {
                                'traffic_down': new_t_down,
                                'traffic_up': new_t_up,
                                'last_connected': new_last_connected,
                            }
                        continue

                    reason = (
                        'banned' if is_banned
                        else ('expired' if is_subscription_expired else 'disabled')
                    )
                    healing_tasks.append({
                        'api_url': server_info['api_url'],
                        'api_key': server_info['api_key'],
                        'peer_id': peer_id,
                        'server_name': server_info['name'],
                        'telegram_id': tg_id,
                        'reason': reason,
                        'target_status': 'disabled',
                    })

                # 🔥 НОВОЕ Направление 2: БД говорит "активен" → API disabled → чиним API
                elif is_active and not local_should_be_disabled and not api_is_active:
                    reverse_healing_tasks.append({
                        'api_url': server_info['api_url'],
                        'api_key': server_info['api_key'],
                        'peer_id': peer_id,
                        'server_name': server_info['name'],
                        'telegram_id': tg_id,
                        'reason': 'api_desync',
                        'target_status': 'active',
                    })

                # Обновляем трафик и last_connected (НО НЕ is_active из API!)
                if (
                    t_down != new_t_down or t_up != new_t_up
                    or last_conn != new_last_connected
                ):
                    updates_data[p_id] = {
                        'traffic_down': new_t_down,
                        'traffic_up': new_t_up,
                        'last_connected': new_last_connected,
                        # 🔥 ИСПРАВЛЕНО: УБРАЛИ 'is_active': api_is_active
                        # БД — источник истины, не перезаписываем из API
                    }

        # Применяем обновления трафика для этого сервера
        if updates_data:
            await _batch_update_profiles(updates_data)
            logger.info(
                f"Трафик синхронизирован для {len(updates_data)} устройств "
                f"на сервере {server_info['name']}"
            )

        # Self-healing: отключаем в API тех, кто должен быть отключён
        if healing_tasks:
            await _self_heal_peers(healing_tasks)

        # Reverse self-healing: включаем в API тех, кто должен быть активен
        if reverse_healing_tasks:
            await _self_heal_peers(reverse_healing_tasks)


async def _batch_update_profiles(updates_data: dict):
    async with session_scope() as session:
        items = list(updates_data.items())
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i:i + BATCH_SIZE]
            batch_ids = [p_id for p_id, _ in batch]

            has_traffic_down = any('traffic_down' in data for _, data in batch)
            has_traffic_up = any('traffic_up' in data for _, data in batch)
            has_last_connected = any('last_connected' in data for _, data in batch)

            values = {}
            if has_traffic_down:
                values['traffic_down'] = case(
                    *[(VPNProfile.id == p_id, data.get('traffic_down', VPNProfile.traffic_down)) for p_id, data in batch],
                    else_=VPNProfile.traffic_down,
                )
            if has_traffic_up:
                values['traffic_up'] = case(
                    *[(VPNProfile.id == p_id, data.get('traffic_up', VPNProfile.traffic_up)) for p_id, data in batch],
                    else_=VPNProfile.traffic_up,
                )
            if has_last_connected:
                values['last_connected'] = case(
                    *[(VPNProfile.id == p_id, data.get('last_connected', VPNProfile.last_connected)) for p_id, data in batch],
                    else_=VPNProfile.last_connected,
                )

            if values:
                stmt = (
                    update(VPNProfile)
                    .where(VPNProfile.id.in_(batch_ids))
                    .values(**values)
                )
                await session.execute(stmt)


async def _self_heal_peers(healing_tasks: list):
    """
    🔥 ИСПРАВЛЕНО: Универсальный self-healing для обоих направлений.
    Может как отключать (disabled), так и включать (active) пиры в API.
    """
    if not healing_tasks:
        return

    total_count = len(healing_tasks)
    if total_count > SELF_HEALING_MAX_PER_CYCLE:
        healing_tasks = healing_tasks[:SELF_HEALING_MAX_PER_CYCLE]

    disabled_count = sum(1 for t in healing_tasks if t['target_status'] == 'disabled')
    activated_count = sum(1 for t in healing_tasks if t['target_status'] == 'active')

    sem = asyncio.Semaphore(10)
    success_count = 0
    fail_count = 0

    async def _patch_peer(task):
        nonlocal success_count, fail_count
        async with sem:
            client = AmneziaClient(task['api_url'], task['api_key'])
            try:
                result = await client.update_client(
                    client_id=task['peer_id'],
                    status=task['target_status']
                )
                if result:
                    success_count += 1
                    logger.info(
                        f"Self-healing: {task['target_status']} peer "
                        f"{task['peer_id'][:16]}... on {task['server_name']} "
                        f"(reason: {task['reason']})"
                    )
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                logger.error(f"Self-healing error: {e}")

    await asyncio.gather(
        *[_patch_peer(t) for t in healing_tasks], return_exceptions=True
    )

    if success_count > 0 or fail_count > 0:
        logger.info(
            f"Self-healing completed: {success_count} success, {fail_count} fail "
            f"(disabled={disabled_count}, activated={activated_count})"
        )