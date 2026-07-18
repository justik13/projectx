import asyncio
import logging
from aiogram import Bot

from .traffic import traffic_sync_loop
from .notifications import subscription_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .payments import stale_payments_checker_loop
from .heartbeat import heartbeat_loop, set_bot_ref

logger = logging.getLogger(__name__)
shutdown_event = asyncio.Event()


async def start_background_workers(bot: Bot) -> list[asyncio.Task]:
    set_bot_ref(bot)

    tasks = [
        asyncio.create_task(traffic_sync_loop(shutdown_event)),
        asyncio.create_task(cleanup_dangling_peers_loop(shutdown_event)),
        asyncio.create_task(stale_payments_checker_loop(bot, shutdown_event)),
        asyncio.create_task(subscription_notifications_loop(bot, shutdown_event)),
        asyncio.create_task(heartbeat_loop(shutdown_event)),
    ]

    logger.info(f"Started {len(tasks)} background workers (incl. heartbeat)")
    return tasks
