import asyncio
import html
import logging
import re
import signal
import traceback

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    ErrorEvent,
    MenuButtonCommands,
)
from aiogram.utils.chat_action import ChatActionMiddleware
from cachetools import TTLCache
from cryptography.fernet import Fernet
from aiohttp import web

from bot import texts
from bot.middlewares import (
    DBSessionMiddleware,
    ThrottlingMiddleware,
    UserContextMiddleware,
    CleanChatMiddleware,
    ActionLockMiddleware,
    CorrelationMiddleware,
    CorrelationFilter,
    PrivateChatMiddleware,
)
from bot.middlewares.ban_check import BanCheckMiddleware
from config.settings import get_settings
from database.connection import close_db, init_db
from services.amnezia_client import close_http_session
from services.yookassa_client import close_yookassa_session
from services.workers import (
    start_background_workers,
    stop_background_workers,
    shutdown_event,
)
from services.workers.heartbeat import set_bot_ref
from bot.handlers.webhook import setup_webhook_routes
from bot.handlers.admin.broadcast import resume_pending_broadcasts

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s - %(levelname)s - "
        "[%(request_id)s] %(name)s: %(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
root_logger.addFilter(CorrelationFilter())
for handler in root_logger.handlers:
    handler.addFilter(CorrelationFilter())

logger = logging.getLogger(__name__)

_error_alert_cache: TTLCache[str, bool] = TTLCache(
    maxsize=10000, ttl=300.0,
)

_SECRET_PATTERNS = [
    (
        re.compile(
            r"(?i)(api[_-]?key|x-api-key|access[_-]?token|"
            r"bot[_-]?token|secret|password|passwd|"
            r"authorization|bearer)\s*[:=]\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)Fernet\([^\)]*\)"),
        "Fernet([REDACTED])",
    ),
    (
        re.compile(
            r"eyJ[A-Za-z0-9_\-]{10,}\."
            r"[A-Za-z0-9_\-]{10,}\."
            r"[A-Za-z0-9_\-]{10,}"
        ),
        "[JWT_REDACTED]",
    ),
    (
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
        "[LONG_TOKEN_REDACTED]",
    ),
    (
        re.compile(r"vpn://[A-Za-z0-9_-]{20,}"),
        "[VPN_URI_REDACTED]",
    ),
]


def _sanitize_text(text: str) -> str:
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def _sanitize_short(text: str, limit: int = 200) -> str:
    sanitized = _sanitize_text(text)
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[:limit] + "..."


async def global_error_handler(
    event: ErrorEvent, **kwargs
) -> bool:
    from bot.middlewares.correlation import (
        get_current_request_id,
    )

    request_id = get_current_request_id()
    exception = event.exception
    error_type = type(exception).__name__

    try:
        tb_lines = traceback.format_exception(
            type(exception),
            exception,
            exception.__traceback__,
        )
        tb_text = "".join(tb_lines)
        tb_sanitized = _sanitize_text(tb_text)
        if len(tb_sanitized) > 4000:
            tb_sanitized = tb_sanitized[:4000] + "\n...[truncated]"
        logger.critical(
            "[%s] Unhandled exception: %s\n%s",
            request_id, error_type, tb_sanitized,
        )
    except Exception:
        logger.critical(
            "[%s] Unhandled exception: %s",
            request_id, error_type,
        )

    state = kwargs.get("state")
    if state:
        try:
            await state.clear()
        except Exception:
            pass

    try:
        settings = get_settings()
        error_type_safe = html.escape(error_type)
        error_short = html.escape(
            _sanitize_short(str(exception), 200)
        )
        error_msg = texts.ALERT_CRITICAL_BOT_ERROR.format(
            request_id=request_id,
            error_type=error_type_safe,
            error_short=error_short,
        )
        alert_key = f"{error_type_safe}:{error_short}"
        if alert_key not in _error_alert_cache:
            _error_alert_cache[alert_key] = True
            for admin_id in settings.ADMIN_IDS:
                try:
                    await event.bot.send_message(
                        admin_id, error_msg,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error(
            "[%s] Failed to send error alert: %s",
            request_id, e,
        )

    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(
                texts.ERROR_TECHNICAL_ALERT,
                show_alert=True,
            )
        elif event.update.message:
            await event.update.message.answer(
                texts.ERROR_TECHNICAL_MESSAGE,
                parse_mode="HTML",
            )
    except Exception:
        pass

    return True


async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(
            command="start",
            description="🚀 Запустить бота",
        ),
    ]
    await bot.set_my_commands(
        commands, scope=BotCommandScopeDefault(),
    )
    await bot.set_chat_menu_button(
        menu_button=MenuButtonCommands(),
    )


async def setup_bot() -> tuple[Bot, Dispatcher]:
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    storage = RedisStorage.from_url(settings.REDIS_URL)
    dp = Dispatcher(storage=storage)

    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())
    dp.message.middleware(PrivateChatMiddleware())
    dp.callback_query.middleware(PrivateChatMiddleware())
    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())
    dp.message.middleware(CleanChatMiddleware())
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ActionLockMiddleware())
    dp.message.middleware(ChatActionMiddleware())

    from bot.handlers.admin.broadcast import (
        router as admin_broadcast_router,
    )
    from bot.handlers.admin.dashboard import (
        router as admin_dashboard_router,
    )
    from bot.handlers.admin.servers import (
        router as admin_servers_router,
    )
    from bot.handlers.admin.tariffs import (
        router as admin_tariffs_router,
    )
    from bot.handlers.admin.users import (
        router as admin_users_router,
    )
    from bot.handlers.connection import (
        router as connection_router,
    )
    from bot.handlers.fallback import router as fallback_router
    from bot.handlers.payment import router as payment_router
    from bot.handlers.profile import router as profile_router
    from bot.handlers.start import router as start_router
    from bot.handlers.support import router as support_router

    for r in [
        start_router,
        profile_router,
        connection_router,
        support_router,
        payment_router,
        admin_dashboard_router,
        admin_users_router,
        admin_servers_router,
        admin_tariffs_router,
        admin_broadcast_router,
        fallback_router,
    ]:
        dp.include_router(r)

    dp.errors.register(global_error_handler)
    await setup_bot_commands(bot)
    return bot, dp


async def start_webhook_server(port: int):
    app = web.Application()
    setup_webhook_routes(app)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info(
        "Webhook server started on 127.0.0.1:%d", port,
    )
    return runner


def _validate_yookassa_config() -> bool:
    settings = get_settings()
    has_shop = bool(settings.YOOKASSA_SHOP_ID.strip())
    has_secret = bool(settings.YOOKASSA_SECRET_KEY.strip())

    if has_shop and not has_secret:
        logger.critical(
            "❌ YOOKASSA_SHOP_ID задан, но "
            "YOOKASSA_SECRET_KEY пуст! "
            "Webhook будет принимать поддельные запросы. "
            "Укажите YOOKASSA_SECRET_KEY в .env."
        )
        return False

    if has_secret and not has_shop:
        logger.critical(
            "❌ YOOKASSA_SECRET_KEY задан, но "
            "YOOKASSA_SHOP_ID пуст! "
            "Укажите оба параметра или удалите оба."
        )
        return False

    return True


async def main():
    settings = get_settings()

    try:
        if not settings.DB_ENCRYPTION_KEY:
            logger.critical("❌ DB_ENCRYPTION_KEY пуст!")
            return

        try:
            Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as e:
            logger.critical(
                "❌ DB_ENCRYPTION_KEY невалиден: %s",
                type(e).__name__,
            )
            return

        if not _validate_yookassa_config():
            return

        logger.info("Инициализация БД...")
        await init_db()

        logger.info(
            "🔄 Bot started — all in-memory operation locks "
            "cleared (restart)."
        )

        bot, dp = await setup_bot()
        set_bot_ref(bot)

        webhook_runner = None
        if (
            settings.YOOKASSA_SHOP_ID
            and settings.YOOKASSA_SECRET_KEY
        ):
            webhook_runner = await start_webhook_server(
                settings.YOOKASSA_WEBHOOK_PORT
            )

        await resume_pending_broadcasts(bot)
        logger.info("Pending broadcasts resumed (if any)")

        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info(
                "Received shutdown signal (SIGTERM/SIGINT)"
            )
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await start_background_workers(bot)

        logger.info("Запуск polling...")
        polling_task = asyncio.create_task(
            dp.start_polling(bot)
        )
        shutdown_task = asyncio.create_task(
            shutdown_event.wait()
        )

        done, pending = await asyncio.wait(
            [polling_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_event.is_set():
            logger.info(
                "Shutdown requested, stopping polling..."
            )
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        else:
            for task in done:
                exc = (
                    task.exception()
                    if not task.cancelled()
                    else None
                )
                if exc:
                    logger.critical(
                        "Fatal error in main task: %s",
                        type(exc).__name__,
                    )

    except Exception as e:
        logger.critical(
            "Fatal error in main: %s", e, exc_info=True,
        )

    finally:
        logger.info("Stopping background workers...")
        try:
            await stop_background_workers()
        except Exception as e:
            logger.error(
                "Error stopping workers: %s", e,
            )

        logger.info("Cleaning up resources...")
        if "webhook_runner" in locals() and webhook_runner:
            await webhook_runner.cleanup()

        await close_http_session()
        await close_yookassa_session()

        try:
            from services.device_service import (
                close_redis as close_device_redis,
            )
            await close_device_redis()
        except Exception as e:
            logger.error(
                "Failed to close device Redis: %s", e,
            )

        try:
            from services.payment_service import (
                close_redis as close_payment_redis,
            )
            await close_payment_redis()
        except Exception as e:
            logger.error(
                "Failed to close payment Redis: %s", e,
            )

        await close_db()
        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())