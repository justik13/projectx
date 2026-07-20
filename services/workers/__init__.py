import asyncio
import logging

from aiogram import Bot

from .traffic import traffic_sync_loop
from .notifications import subscription_notifications_loop
from .cleanup import cleanup_dangling_peers_loop
from .payments import stale_payments_checker_loop
from .heartbeat import heartbeat_loop, set_bot_ref
from config.settings import get_settings

logger = logging.getLogger(__name__)

shutdown_event = asyncio.Event()

_worker_tasks: dict[str, asyncio.Task] = {}
_supervisor_task: asyncio.Task | None = None

_SUPERVISOR_CHECK_INTERVAL = 15.0
_MAX_WORKER_RESTARTS = 10


def _traffic_worker_factory(bot: Bot):
    return traffic_sync_loop(shutdown_event)


def _cleanup_worker_factory(bot: Bot):
    return cleanup_dangling_peers_loop(shutdown_event)


def _stale_payments_worker_factory(bot: Bot):
    return stale_payments_checker_loop(bot, shutdown_event)


def _notifications_worker_factory(bot: Bot):
    return subscription_notifications_loop(bot, shutdown_event)


def _heartbeat_worker_factory(bot: Bot):
    return heartbeat_loop(shutdown_event)


_WORKER_FACTORIES = {
    "traffic": _traffic_worker_factory,
    "cleanup": _cleanup_worker_factory,
    "stale_payments": _stale_payments_worker_factory,
    "notifications": _notifications_worker_factory,
    "heartbeat": _heartbeat_worker_factory,
}


async def _send_worker_crash_alert(
    bot: Bot,
    worker_name: str,
    error_text: str,
):
    try:
        settings = get_settings()

        message = (
            "🚨 <b>Фоновый воркер упал</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🧩 <b>Воркер:</b> <code>{worker_name}</code>\n"
            f"⚠️ <b>Ошибка:</b> <code>{error_text}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Supervisor пытается перезапустить воркер.</i>"
        )

        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    message,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    except Exception as e:
        logger.error("Failed to send worker crash alert: %s", e)


async def _supervise_workers(bot: Bot):
    """
    Supervisor фоновых воркеров.

    Если воркер падает:
    1. Пишем critical log.
    2. Отправляем алерт админам.
    3. Перезапускаем воркер.

    Если воркер упал слишком много раз подряд:
    - останавливаем перезапуски;
    - отправляем критический алерт.
    """
    restart_counts: dict[str, int] = {}

    logger.info("Worker supervisor started")

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=_SUPERVISOR_CHECK_INTERVAL,
            )
            break
        except asyncio.TimeoutError:
            pass

        for worker_name, task in list(_worker_tasks.items()):
            if shutdown_event.is_set():
                break

            if not task.done():
                continue

            factory = _WORKER_FACTORIES.get(worker_name)

            if factory is None:
                continue

            error_text = "unknown"

            if task.cancelled():
                logger.info(
                    "Worker %s was cancelled, not restarting",
                    worker_name,
                )
                continue

            try:
                exc = task.exception()
            except Exception as e:
                exc = e

            if exc:
                error_text = type(exc).__name__

            logger.critical(
                "Worker %s died unexpectedly: %s",
                worker_name,
                error_text,
            )

            await _send_worker_crash_alert(bot, worker_name, error_text)

            restart_counts[worker_name] = (
                restart_counts.get(worker_name, 0) + 1
            )
            count = restart_counts[worker_name]

            if count > _MAX_WORKER_RESTARTS:
                logger.critical(
                    "Worker %s exceeded max restart count (%s). "
                    "Not restarting anymore.",
                    worker_name,
                    _MAX_WORKER_RESTARTS,
                )

                try:
                    settings = get_settings()

                    critical_message = (
                        "🚨 <b>Фоновый воркер не удалось восстановить</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        f"🧩 <b>Воркер:</b> <code>{worker_name}</code>\n"
                        f"🔁 <b>Попыток перезапуска:</b> {count}\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "<i>Требуется ручное вмешательство.</i>"
                    )

                    for admin_id in settings.ADMIN_IDS:
                        try:
                            await bot.send_message(
                                admin_id,
                                critical_message,
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass

                except Exception as e:
                    logger.error(
                        "Failed to send critical worker alert: %s",
                        e,
                    )

                continue

            backoff = min(30.0, 2.0 ** count)

            logger.warning(
                "Restarting worker %s in %.1f seconds (restart #%s)",
                worker_name,
                backoff,
                count,
            )

            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=backoff,
                )
                break
            except asyncio.TimeoutError:
                pass

            if shutdown_event.is_set():
                break

            _worker_tasks[worker_name] = asyncio.create_task(
                factory(bot),
                name=f"worker_{worker_name}",
            )

    logger.info("Worker supervisor stopped")


async def start_background_workers(bot: Bot) -> list[asyncio.Task]:
    global _supervisor_task

    set_bot_ref(bot)

    _worker_tasks.clear()

    for worker_name, factory in _WORKER_FACTORIES.items():
        _worker_tasks[worker_name] = asyncio.create_task(
            factory(bot),
            name=f"worker_{worker_name}",
        )

    _supervisor_task = asyncio.create_task(
        _supervise_workers(bot),
        name="worker_supervisor",
    )

    logger.info(
        "Started %s background workers + supervisor",
        len(_worker_tasks),
    )

    return list(_worker_tasks.values()) + [_supervisor_task]


async def stop_background_workers():
    global _supervisor_task

    shutdown_event.set()

    if _supervisor_task is not None:
        _supervisor_task.cancel()

        try:
            await _supervisor_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error while stopping supervisor: %s", e)

        _supervisor_task = None

    tasks = list(_worker_tasks.values())

    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.wait(tasks, timeout=10.0)

    _worker_tasks.clear()

    logger.info("Background workers stopped")