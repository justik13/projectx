import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from database.connection import session_scope
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, Server, User
from bot.constants import (
    TRAFFIC_SYNC_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL,
    SELF_HEALING_MAX_PER_CYCLE,
)
from utils.datetime_helpers import now_utc
from config.settings import get_settings

logger = logging.getLogger("BackgroundWorker")

BATCH_SIZE = 100
REVERSE_HEALING_WINDOW_SECONDS = 300
TRAFFIC_QUOTA_BYTES = 1 * 1024 * 1024 * 1024 * 1024
TRAFFIC_MAX_BACKOFF = 900

_quota_alerted: set[int] = set()
_consecutive_crashes: int = 0


async def traffic_sync_loop(shutdown_event: asyncio.Event):
    global _consecutive_crashes

    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=TRAFFIC_SYNC_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                pass

            servers = []
            async with session_scope() as session:
                stmt = select(
                    Server.id, Server.api_url, Server.api_key,
                    Server.name, Server.is_active
                )
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

            for server_info in servers:
                server_id = server_info['id']
                if server_id not in api_data_by_server:
                    continue
                api_clients = api_data_by_server[server_id]
                await _process_server_traffic(server_info, api_clients)

            _consecutive_crashes = 0

        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            _consecutive_crashes += 1
            backoff = min(
                WORKER_ERROR_SLEEP_INTERVAL * (2 ** min(_consecutive_crashes - 1, 4)),
                TRAFFIC_MAX_BACKOFF,
            )
            logger.error(
                f"Критическая ошибка в цикле трафика "
                f"(crash #{_consecutive_crashes}, next retry in {backoff}s): {e}",
                exc_info=True,
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(backoff)

    logger.info("Traffic sync worker stopped gracefully")


async def _process_server_traffic(server_info, api_clients):
    server_id = server_info['id']

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
            .execution_options(yield_per=BATCH_SIZE)
        )

        result = await session.stream(stmt)

        updates_data = {}
        healing_tasks = []
        reverse_healing_tasks = []
        current_time = now_utc()

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
                        new_last_connected = datetime.fromtimestamp(ts, tz=timezone.utc)
                    except (ValueError, TypeError, OverflowError):
                        pass

                api_is_active = (api_data.status == "active")
                is_subscription_expired = sub_end and sub_end < current_time
                local_should_be_disabled = (
                    (not is_active) or is_banned or is_subscription_expired
                )

                if local_should_be_disabled and api_is_active:
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
                elif is_active and not local_should_be_disabled and not api_is_active:
                    api_updated = api_data.updatedAt
                    if api_updated:
                        try:
                            ts = int(float(str(api_updated)))
                            if ts > 1e12:
                                ts = ts // 1000
                            updated_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                            time_since_update = (current_time - updated_dt).total_seconds()
                            if time_since_update < REVERSE_HEALING_WINDOW_SECONDS:
                                reverse_healing_tasks.append({
                                    'api_url': server_info['api_url'],
                                    'api_key': server_info['api_key'],
                                    'peer_id': peer_id,
                                    'server_name': server_info['name'],
                                    'telegram_id': tg_id,
                                    'reason': 'api_desync',
                                    'target_status': 'active',
                                })
                            else:
                                logger.info(
                                    f"Reverse healing skipped: peer {peer_id[:16]}... "
                                    f"disabled {time_since_update:.0f}s ago "
                                    f"(likely manual admin action)"
                                )
                        except (ValueError, TypeError, OverflowError):
                            logger.debug(
                                f"Reverse healing: failed to parse updatedAt "
                                f"for peer {peer_id[:16]}..."
                            )
                    else:
                        logger.debug(
                            f"Reverse healing: no updatedAt for peer {peer_id[:16]}..., "
                            f"cannot assess freshness"
                        )

                if (
                    t_down != new_t_down or t_up != new_t_up
                    or last_conn != new_last_connected
                ):
                    updates_data[p_id] = {
                        'traffic_down': new_t_down,
                        'traffic_up': new_t_up,
                        'last_connected': new_last_connected,
                    }

                total_traffic = (new_t_down or 0) + (new_t_up or 0)
                if total_traffic > TRAFFIC_QUOTA_BYTES and p_id not in _quota_alerted:
                    _quota_alerted.add(p_id)
                    asyncio.create_task(
                        _send_quota_alert(tg_id, server_info['name'], total_traffic, p_id)
                    )

        if updates_data:
            await _batch_update_profiles(updates_data)
            logger.info(
                f"Трафик синхронизирован для {len(updates_data)} устройств "
                f"на сервере {server_info['name']}"
            )

        if healing_tasks:
            await _self_heal_peers(healing_tasks)

        if reverse_healing_tasks:
            await _self_heal_peers(reverse_healing_tasks)


async def _send_quota_alert(telegram_id: int, server_name: str, total_bytes: int, profile_id: int):
    try:
        from services.workers.heartbeat import get_bot_ref
        bot = get_bot_ref()
        if not bot:
            return

        settings = get_settings()
        admin_ids = settings.ADMIN_IDS
        if not admin_ids:
            return

        tb = total_bytes / (1024 ** 4)
        msg = (
            f"⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 <b>Пользователь:</b> <code>{telegram_id}</code>\n"
            f"🌍 <b>Сервер:</b> {server_name}\n"
            f"📊 <b>Использовано:</b> <b>{tb:.2f} TB</b>\n"
            f"🆔 <b>Profile ID:</b> <code>{profile_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Пользователь скачал более 1 TB трафика.\n"
            f"Рекомендуется связаться с ним или принять меры.\n"
            f"Доступ НЕ отключен автоматически (Fair Usage Policy).</i>"
        )

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(
            text="👤 Профиль пользователя",
            callback_data=f"admin_user_card:{telegram_id}"
        )
        builder.adjust(1)

        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id, msg,
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Failed to send quota alert to {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Failed to send quota alert: {e}")


async def _batch_update_profiles(updates_data: dict):
    async with session_scope() as session:
        items = list(updates_data.items())
        for i in range(0, len(items), BATCH_SIZE):
            batch = items[i:i + BATCH_SIZE]
            batch_values = []
            for p_id, data in batch:
                row = {'id': p_id}
                if 'traffic_down' in data:
                    row['traffic_down'] = data['traffic_down']
                if 'traffic_up' in data:
                    row['traffic_up'] = data['traffic_up']
                if 'last_connected' in data:
                    row['last_connected'] = data['last_connected']
                batch_values.append(row)

            if not batch_values:
                continue

            stmt = insert(VPNProfile).values(batch_values)
            update_dict = {}
            if any('traffic_down' in data for _, data in batch):
                update_dict['traffic_down'] = stmt.excluded.traffic_down
            if any('traffic_up' in data for _, data in batch):
                update_dict['traffic_up'] = stmt.excluded.traffic_up
            if any('last_connected' in data for _, data in batch):
                update_dict['last_connected'] = stmt.excluded.last_connected

            if update_dict:
                stmt = stmt.on_conflict_do_update(
                    index_elements=['id'],
                    set_=update_dict
                )
            await session.execute(stmt)


async def _self_heal_peers(healing_tasks: list):
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