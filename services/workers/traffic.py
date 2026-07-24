import asyncio
import logging
from datetime import datetime, timezone

from cachetools import TTLCache
from sqlalchemy import select, update

from bot.constants import SELF_HEALING_MAX_PER_CYCLE, TRAFFIC_SYNC_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import queue_post_commit_task, session_scope
from database.models import Server, User, VPNProfile
from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

BATCH_SIZE = 100
TRAFFIC_QUOTA_BYTES = 1 * 1024 * 1024 * 1024 * 1024
TRAFFIC_MAX_BACKOFF = 900
WORKER_START_DELAY = 30.0

# ИСПРАВЛЕНО: TTLCache вместо бесконечного set.
_quota_alerted: TTLCache[int, bool] = TTLCache(maxsize=10000, ttl=86400)

_consecutive_crashes: int = 0
_background_tasks: set[asyncio.Task] = set()


def _start_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def traffic_sync_loop(shutdown_event: asyncio.Event):
    global _consecutive_crashes

    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=WORKER_START_DELAY)
        logger.info("Traffic sync worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            await _traffic_sync_once()
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
                "Критическая ошибка в цикле трафика (crash #%s, next retry in %ss): %s",
                _consecutive_crashes, backoff, e, exc_info=True,
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(backoff)
            continue

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=TRAFFIC_SYNC_INTERVAL)
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Traffic sync worker stopped gracefully")


async def _traffic_sync_once():
    servers = []
    async with session_scope() as session:
        stmt = select(Server.id, Server.api_url, Server.api_key, Server.name, Server.is_active)
        result = await session.execute(stmt)
        servers = [
            {"id": row[0], "api_url": row[1], "api_key": row[2], "name": row[3], "is_active": row[4]}
            for row in result.all()
        ]

    if not servers:
        return

    async def _fetch_server_traffic(server_info):
        client = AmneziaClient(server_info["api_url"], server_info["api_key"])
        try:
            api_clients_list = await client.get_all_clients()
            if api_clients_list is None:
                return server_info["id"], None
            return server_info["id"], {c.id: c for c in api_clients_list}
        except Exception as e:
            logger.error("Ошибка трафика с %s: %s", server_info["name"], e)
            return server_info["id"], None

    tasks = [_fetch_server_traffic(s) for s in servers]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    api_data_by_server = {
        r[0]: r[1] for r in results
        if not isinstance(r, Exception) and r is not None and r[1] is not None
    }

    for server_info in servers:
        server_id = server_info["id"]
        if server_id not in api_data_by_server:
            continue
        api_clients = api_data_by_server[server_id]
        await _process_server_traffic(server_info, api_clients)


async def _process_server_traffic(server_info, api_clients):
    server_id = server_info["id"]
    updates_data = {}
    healing_tasks = []
    reverse_healing_tasks = []
    disable_db_ids = []
    current_time = now_utc()

    async with session_scope() as session:
        stmt = (
            select(
                VPNProfile.id, VPNProfile.peer_id, VPNProfile.traffic_down,
                VPNProfile.traffic_up, VPNProfile.last_connected,
                VPNProfile.is_active, User.is_banned, User.telegram_id,
                User.subscription_end,
            )
            .join(User, VPNProfile.user_id == User.id)
            .where(VPNProfile.server_id == server_id)
        )
        result = await session.execute(stmt)
        rows = result.all()

        for (p_id, peer_id, t_down, t_up, last_conn, is_active, is_banned, tg_id, sub_end) in rows:
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

            api_is_active = api_data.status == "active"
            is_subscription_expired = sub_end is None or sub_end < current_time
            local_should_be_disabled = (not is_active) or is_banned or is_subscription_expired

            if local_should_be_disabled:
                if is_active:
                    disable_db_ids.append(p_id)
                if api_is_active:
                    reason = "banned" if is_banned else ("expired" if is_subscription_expired else "disabled")
                    healing_tasks.append({
                        "profile_id": p_id, "api_url": server_info["api_url"],
                        "api_key": server_info["api_key"], "peer_id": peer_id,
                        "server_name": server_info["name"], "telegram_id": tg_id,
                        "reason": reason, "target_status": "disabled",
                        "expires_at": None, "clear_expires_at": False,
                    })
            elif is_active and not api_is_active:
                expires_ts = None
                if sub_end and sub_end.year < 2100:
                    expires_ts = int(sub_end.timestamp())
                reverse_healing_tasks.append({
                    "profile_id": p_id, "api_url": server_info["api_url"],
                    "api_key": server_info["api_key"], "peer_id": peer_id,
                    "server_name": server_info["name"], "telegram_id": tg_id,
                    "reason": "api_desync", "target_status": "active",
                    "expires_at": expires_ts, "clear_expires_at": expires_ts is None,
                })

            if t_down != new_t_down or t_up != new_t_up or last_conn != new_last_connected:
                updates_data[p_id] = {
                    "traffic_down": new_t_down, "traffic_up": new_t_up,
                    "last_connected": new_last_connected,
                }

            total_traffic = (new_t_down or 0) + (new_t_up or 0)
            if total_traffic > TRAFFIC_QUOTA_BYTES and p_id not in _quota_alerted:
                _quota_alerted[p_id] = True
                _start_background_task(_send_quota_alert(tg_id, server_info["name"], total_traffic, p_id))

        if updates_data:
            for profile_id, data in updates_data.items():
                values = {}
                if "traffic_down" in data:
                    values["traffic_down"] = data["traffic_down"]
                if "traffic_up" in data:
                    values["traffic_up"] = data["traffic_up"]
                if "last_connected" in data:
                    values["last_connected"] = data["last_connected"]
                if values:
                    await session.execute(
                        update(VPNProfile).where(VPNProfile.id == profile_id).values(**values)
                    )

        if disable_db_ids:
            await session.execute(
                update(VPNProfile).where(VPNProfile.id.in_(disable_db_ids)).values(is_active=False)
            )

        if healing_tasks:
            queue_post_commit_task(session, lambda tasks=healing_tasks: _self_heal_peers(tasks))
        if reverse_healing_tasks:
            queue_post_commit_task(session, lambda tasks=reverse_healing_tasks: _self_heal_peers(tasks))


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
            f"{'─' * 20}\n"
            f"👤 <b>Пользователь:</b> <code>{telegram_id}</code>\n"
            f"🌍 <b>Сервер:</b> {server_name}\n"
            f"📊 <b>Использовано:</b> <b>{tb:.2f} TB</b>\n"
            f"🆔 <b>Profile ID:</b> <code>{profile_id}</code>\n"
            f"{'─' * 20}\n"
            f"<i>Пользователь скачал более 1 TB трафика.\n"
            f"Рекомендуется связаться с ним или принять меры.\n"
            f"Доступ НЕ отключён автоматически (Fair Usage Policy).</i>"
        )

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="👤 Профиль пользователя", callback_data=f"admin_user_card:{telegram_id}")
        builder.adjust(1)

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, msg, reply_markup=builder.as_markup(), parse_mode="HTML")
            except Exception as e:
                logger.error("Failed to send quota alert to %s: %s", admin_id, e)
    except Exception as e:
        logger.error("Failed to send quota alert: %s", e)


async def _self_heal_peers(healing_tasks: list):
    if not healing_tasks:
        return

    if len(healing_tasks) > SELF_HEALING_MAX_PER_CYCLE:
        healing_tasks = healing_tasks[:SELF_HEALING_MAX_PER_CYCLE]

    sem = asyncio.Semaphore(10)
    success_count = 0
    fail_count = 0

    async def _patch_peer(task):
        nonlocal success_count, fail_count
        async with sem:
            client = AmneziaClient(task["api_url"], task["api_key"])
            try:
                result = await client.update_client(
                    client_id=task["peer_id"],
                    status=task["target_status"],
                    expires_at=task.get("expires_at"),
                    clear_expires_at=task.get("clear_expires_at", False),
                )
                if result:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                fail_count += 1
                logger.error("Self-healing error: %s", e)

    await asyncio.gather(*[_patch_peer(t) for t in healing_tasks], return_exceptions=True)

    if success_count > 0 or fail_count > 0:
        logger.info("Self-healing completed: %s success, %s fail", success_count, fail_count)