"""
Фоновый воркер синхронизации трафика.

🔥 ИСПРАВЛЕНО (Часть 3):
- Batch UPDATE: 100 записей за один запрос вместо N отдельных
- fix: totalDownload or old_value больше не съедает легитимный 0
- Self-Healing учитывает истёкшие подписки
"""

import asyncio
import logging
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy import select, update, case
from database.connection import get_session
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, Server, User
from bot.constants import (
    TRAFFIC_SYNC_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL,
    SELF_HEALING_MAX_PER_CYCLE,
)

logger = logging.getLogger("BackgroundWorker")

# Размер батча для batch UPDATE (оптимально для SQLite)
BATCH_SIZE = 100


async def traffic_sync_loop(shutdown_event: asyncio.Event):
    """
    Фоновый воркер синхронизации трафика.
    
    🔥 ИСПРАВЛЕНО (Часть 3):
    - Batch UPDATE: CASE-based update для 100 записей за раз
    - fix: totalDownload or old_value → используем explicit None check
    """
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=TRAFFIC_SYNC_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                pass

            session = await get_session()
            try:
                stmt = (
                    select(
                        VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id,
                        VPNProfile.traffic_down, VPNProfile.traffic_up,
                        VPNProfile.last_connected, VPNProfile.is_active,
                        Server.api_url, Server.api_key, Server.name,
                        Server.is_active.label('server_is_active'),
                        User.is_banned, User.telegram_id, User.subscription_end,
                    )
                    .join(Server, VPNProfile.server_id == Server.id)
                    .join(User, VPNProfile.user_id == User.id)
                )
                result = await session.execute(stmt)
                rows = result.all()

                by_server = defaultdict(list)
                servers_map = {}

                for row in rows:
                    (
                        p_id, peer_id, s_id, t_down, t_up, last_conn, is_active,
                        api_url, api_key, s_name, server_is_active,
                        is_banned, tg_id, sub_end,
                    ) = row

                    by_server[s_id].append({
                        'id': p_id, 'peer_id': peer_id,
                        'traffic_down': t_down, 'traffic_up': t_up,
                        'last_connected': last_conn, 'is_active': is_active,
                        'server_is_active': server_is_active,
                        'is_banned': is_banned, 'telegram_id': tg_id,
                        'subscription_end': sub_end,
                    })
                    servers_map[s_id] = {
                        'api_url': api_url, 'api_key': api_key, 'name': s_name,
                    }
            finally:
                await session.close()

            if not servers_map:
                continue

            async def _fetch_server_traffic(server_id, server_info):
                client = AmneziaClient(
                    server_info['api_url'], server_info['api_key']
                )
                try:
                    api_clients_list = await client.get_all_clients()
                    if api_clients_list is None:
                        return server_id, None
                    return server_id, {c.id: c for c in api_clients_list}
                except Exception as e:
                    logger.error(
                        f"Ошибка трафика с {server_info['name']}: {e}"
                    )
                    return server_id, None

            tasks = [
                _fetch_server_traffic(s_id, servers_map[s_id])
                for s_id in servers_map
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            api_data_by_server = {
                r[0]: r[1]
                for r in results
                if not isinstance(r, Exception) and r is not None and r[1] is not None
            }

            updates_data = {}
            healing_tasks = []
            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

            for server_id, api_clients in api_data_by_server.items():
                server_info = servers_map[server_id]

                for p_dict in by_server[server_id]:
                    if p_dict['peer_id'] not in api_clients:
                        continue

                    api_data = api_clients[p_dict['peer_id']]

                    # 🔥 ИСПРАВЛЕНО (Часть 3): explicit None check вместо "or"
                    # Было: t_down = api_data.traffics.totalDownload or p_dict['traffic_down']
                    # Это съедало легитимный 0 (если трафик реально 0)
                    # Стало: проверяем None явно
                    api_t_down = api_data.traffics.totalDownload
                    api_t_up = api_data.traffics.totalUpload

                    t_down = (
                        api_t_down if api_t_down is not None
                        else p_dict['traffic_down']
                    )
                    t_up = (
                        api_t_up if api_t_up is not None
                        else p_dict['traffic_up']
                    )

                    last_conn_raw = (
                        api_data.lastHandshake
                        or api_data.lastSeen
                        or api_data.updatedAt
                    )
                    last_connected = p_dict['last_connected']
                    if last_conn_raw:
                        try:
                            ts = int(float(str(last_conn_raw)))
                            if ts > 1e12:
                                ts = ts // 1000
                            last_connected = datetime.fromtimestamp(
                                ts, tz=timezone.utc
                            ).replace(tzinfo=None)
                        except (ValueError, TypeError, OverflowError):
                            pass

                    api_is_active = (api_data.status == "active")
                    db_is_active = p_dict['is_active']
                    user_is_banned = p_dict['is_banned']
                    server_is_active = p_dict['server_is_active']
                    sub_end = p_dict['subscription_end']

                    is_subscription_expired = sub_end and sub_end < now_utc
                    local_should_be_disabled = (
                        (not db_is_active) or user_is_banned or is_subscription_expired
                    )

                    if local_should_be_disabled and api_is_active:
                        if not server_is_active:
                            logger.debug(
                                f"Self-healing skipped: peer {p_dict['peer_id'][:16]}... "
                                f"on {server_info['name']} — server is disabled by admin"
                            )
                            if (
                                p_dict['traffic_down'] != t_down
                                or p_dict['traffic_up'] != t_up
                                or p_dict['last_connected'] != last_connected
                            ):
                                updates_data[p_dict['id']] = {
                                    'traffic_down': t_down,
                                    'traffic_up': t_up,
                                    'last_connected': last_connected,
                                }
                            continue

                        reason = (
                            'banned' if user_is_banned
                            else ('expired' if is_subscription_expired else 'disabled')
                        )
                        healing_tasks.append({
                            'api_url': server_info['api_url'],
                            'api_key': server_info['api_key'],
                            'peer_id': p_dict['peer_id'],
                            'server_name': server_info['name'],
                            'telegram_id': p_dict['telegram_id'],
                            'reason': reason,
                        })

                        if (
                            p_dict['traffic_down'] != t_down
                            or p_dict['traffic_up'] != t_up
                            or p_dict['last_connected'] != last_connected
                        ):
                            updates_data[p_dict['id']] = {
                                'traffic_down': t_down,
                                'traffic_up': t_up,
                                'last_connected': last_connected,
                            }
                        continue

                    if (
                        p_dict['traffic_down'] != t_down
                        or p_dict['traffic_up'] != t_up
                        or p_dict['last_connected'] != last_connected
                        or db_is_active != api_is_active
                    ):
                        updates_data[p_dict['id']] = {
                            'traffic_down': t_down,
                            'traffic_up': t_up,
                            'last_connected': last_connected,
                            'is_active': api_is_active,
                        }

            # 🔥 ИСПРАВЛЕНО (Часть 3): Batch UPDATE
            if updates_data:
                await _batch_update_profiles(updates_data)
                logger.info(
                    f"Трафик синхронизирован для {len(updates_data)} устройств "
                    f"({(len(updates_data) + BATCH_SIZE - 1) // BATCH_SIZE} batches)"
                )

            if healing_tasks:
                await _self_heal_disabled_peers(healing_tasks)

        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            logger.error(
                f"Критическая ошибка в цикле трафика: {e}", exc_info=True
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

    logger.info("Traffic sync worker stopped gracefully")


async def _batch_update_profiles(updates_data: dict):
    """
    🔥 ИСПРАВЛЕНО (Часть 3): Batch UPDATE через CASE-based query.
    
    Было: 750 отдельных UPDATE запросов за цикл
    Стало: ~8 запросов (по 100 записей каждый)
    
    Для SQLite это даёт ~10x ускорение.
    """
    session = await get_session()
    try:
        items = list(updates_data.items())

        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i:i + BATCH_SIZE]
            batch_ids = [p_id for p_id, _ in batch]

            # Определяем какие поля есть в батче
            has_traffic_down = any('traffic_down' in data for _, data in batch)
            has_traffic_up = any('traffic_up' in data for _, data in batch)
            has_last_connected = any('last_connected' in data for _, data in batch)
            has_is_active = any('is_active' in data for _, data in batch)

            values = {}

            if has_traffic_down:
                values['traffic_down'] = case(
                    *[
                        (VPNProfile.id == p_id, data.get('traffic_down', VPNProfile.traffic_down))
                        for p_id, data in batch
                    ],
                    else_=VPNProfile.traffic_down,
                )

            if has_traffic_up:
                values['traffic_up'] = case(
                    *[
                        (VPNProfile.id == p_id, data.get('traffic_up', VPNProfile.traffic_up))
                        for p_id, data in batch
                    ],
                    else_=VPNProfile.traffic_up,
                )

            if has_last_connected:
                values['last_connected'] = case(
                    *[
                        (VPNProfile.id == p_id, data.get('last_connected', VPNProfile.last_connected))
                        for p_id, data in batch
                    ],
                    else_=VPNProfile.last_connected,
                )

            if has_is_active:
                values['is_active'] = case(
                    *[
                        (VPNProfile.id == p_id, data.get('is_active', VPNProfile.is_active))
                        for p_id, data in batch
                    ],
                    else_=VPNProfile.is_active,
                )

            if values:
                stmt = (
                    update(VPNProfile)
                    .where(VPNProfile.id.in_(batch_ids))
                    .values(**values)
                )
                await session.execute(stmt)

        await session.commit()
    except Exception as e:
        logger.error(f"Batch update failed: {e}", exc_info=True)
        await session.rollback()
    finally:
        await session.close()


async def _self_heal_disabled_peers(healing_tasks: list):
    """🔥 Self-Healing: принудительно отключает на API клиентов."""
    if not healing_tasks:
        return

    total_count = len(healing_tasks)
    if total_count > SELF_HEALING_MAX_PER_CYCLE:
        logger.warning(
            f"Self-healing: {total_count} peers need healing, "
            f"limiting to {SELF_HEALING_MAX_PER_CYCLE} per cycle"
        )
        healing_tasks = healing_tasks[:SELF_HEALING_MAX_PER_CYCLE]

    sem = asyncio.Semaphore(10)
    success_count = 0
    fail_count = 0

    async def _patch_peer(task):
        nonlocal success_count, fail_count
        async with sem:
            client = AmneziaClient(task['api_url'], task['api_key'])
            try:
                result = await client.update_client(
                    client_id=task['peer_id'], status="disabled"
                )
                if result:
                    success_count += 1
                    logger.info(
                        f"Self-healing: disabled peer {task['peer_id'][:16]}... "
                        f"on {task['server_name']} (user={task['telegram_id']}, "
                        f"reason={task['reason']})"
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
            f"Self-healing completed: {success_count} success, {fail_count} fail"
        )
