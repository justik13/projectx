import asyncio
from aiogram import Bot
from .traffic import traffic_sync_loop
from .notifications import subscription_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .payments import stale_payments_checker_loop


async def start_background_worker(bot: Bot):
    asyncio.create_task(traffic_sync_loop())
    asyncio.create_task(cleanup_dangling_peers_loop())
    asyncio.create_task(stale_payments_checker_loop(bot))
    asyncio.create_task(subscription_notifications_loop(bot))