import asyncio
import logging
from aiogram import Bot
from .traffic import traffic_sync_loop
from .notifications import subscription_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .payments import stale_payments_checker_loop
from .heartbeat import heartbeat_loop, set_bot_ref
from .soft_delete import soft_delete_cleanup_loop

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО #5 (из Части 3): Graceful shutdown
shutdown_event = asyncio.Event()


async def start_background_workers(bot: Bot) -> list[asyncio.Task]:
    """
    Запускает все background workers и возвращает список задач.

    🔥 ИСПРАВЛЕНО #7: Передаём bot в heartbeat для алертов CircuitBreaker.

    Workers:
    1. traffic_sync_loop — синхронизация трафика каждые 15 мин
    2. cleanup_dangling_peers_loop — очистка призраков раз в 24ч
    3. stale_payments_checker_loop — проверка зависших платежей каждый час
    4. subscription_notifications_loop — уведомления о скором истечении каждые 30 мин
    5. heartbeat_loop — обновление timestamp + мониторинг CB каждые 60с
    6. soft_delete_cleanup_loop — очистка soft-deleted пользователей раз в 24ч
    """
    # 🔥 ИСПРАВЛЕНО #7: Устанавливаем ссылку на bot для алертов
    set_bot_ref(bot)

    tasks = [
        asyncio.create_task(traffic_sync_loop(shutdown_event)),
        asyncio.create_task(cleanup_dangling_peers_loop(shutdown_event)),
        asyncio.create_task(stale_payments_checker_loop(bot, shutdown_event)),
        asyncio.create_task(subscription_notifications_loop(bot, shutdown_event)),
        asyncio.create_task(heartbeat_loop(shutdown_event)),
        asyncio.create_task(soft_delete_cleanup_loop(shutdown_event)),
    ]

    logger.info(f"Started {len(tasks)} background workers (incl. heartbeat + soft delete cleanup)")
    return tasks