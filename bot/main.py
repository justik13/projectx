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
from services.workers import (
    start_background_workers,
    stop_background_workers,
    shutdown_event,
)
from bot.handlers.webhook import setup_webhook_routes
from bot.handlers.admin.broadcast import resume_pending_broadcasts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(request_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
root_logger.addFilter(CorrelationFilter())

for handler in root_logger.handlers:
    handler.addFilter(CorrelationFilter())

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = [
    (
        re.compile(
            r"(?i)(api[_-]?key|x-api-key|access[_-]?token|bot[_-]?token|"
            r"secret|password|passwd|authorization|bearer)\s*[:=]\s*\S+"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(r"(?i)Fernet\([^\)]*\)"),
        "Fernet([REDACTED])",
    ),
    (
        re.compile(
            r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
        ),
        "[JWT_REDACTED]",
    ),
    (
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
        "[LONG_TOKEN_REDACTED]",
    ),
]


def _sanitize_text(text: str) -> str:
    """
    Удаляет потенциальные секреты из текста исключения/трейсбека.

    Это нужно, чтобы:
    - API-ключи;
    - токены;
    - секреты;
    - пароли;
    - длинные base64/JWT-подобные строки

    не попадали в journalctl и админские алерты.
    """
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


async def global_error_handler(event: ErrorEvent, **kwargs) -> bool:
    from bot.middlewares.correlation import get_current_request_id

    request_id = get_current_request_id()

    exception = event.exception
    error_type = type(exception).__name__

    # Логируем санитизированный traceback, а не сырой текст исключения.
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
            request_id,
            error_type,
            tb_sanitized,
        )
    except Exception:
        logger.critical(
            "[%s] Unhandled exception: %s",
            request_id,
            error_type,
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

        error_msg = (
            f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА БОТА</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 <b>Request ID:</b> <code>{request_id}</code>\n"
            f"⚠️ <b>Тип:</b> <code>{error_type_safe}</code>\n"
            f"📝 <b>Описание:</b> <i>{error_short}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<i>Полный лог доступен через:\n"
            f"<code>journalctl -u projectx-bot | grep {request_id}</code></i>"
        )

        for admin_id in settings.ADMIN_IDS:
            try:
                await event.bot.send_message(
                    admin_id,
                    error_msg,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    except Exception as e:
        logger.error("[%s] Failed to send error alert: %s", request_id, e)

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
        BotCommand(command="start", description="🚀 Запустить бота"),
    ]

    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    logger.info("Bot commands configured")


async def setup_bot() -> tuple[Bot, Dispatcher]:
    settings = get_settings()

    bot = Bot(token=settings.BOT_TOKEN)

    storage = RedisStorage.from_url(settings.REDIS_URL)

    dp = Dispatcher(storage=storage)

    # Correlation/request_id.
    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())
    dp.pre_checkout_query.middleware(CorrelationMiddleware())

    # Private chat only.
    #
    # Важно: этот middleware должен быть ДО CleanChatMiddleware,
    # чтобы бот не удалял сообщения в группах.
    dp.message.middleware(PrivateChatMiddleware())
    dp.callback_query.middleware(PrivateChatMiddleware())

    # DB session.
    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())
    dp.pre_checkout_query.middleware(DBSessionMiddleware())

    # Clean chat only for private messages.
    dp.message.middleware(CleanChatMiddleware())

    # User context and ban checks.
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())

    # Throttling and action locks.
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    dp.callback_query.middleware(ActionLockMiddleware())

    # UX helper.
    dp.message.middleware(ChatActionMiddleware())

    from bot.handlers.admin.broadcast import router as admin_broadcast_router
    from bot.handlers.admin.dashboard import router as admin_dashboard_router
    from bot.handlers.admin.servers import router as admin_servers_router
    from bot.handlers.admin.tariffs import router as admin_tariffs_router
    from bot.handlers.admin.users import router as admin_users_router
    from bot.handlers.connection import router as connection_router
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

    logger.info("Webhook server started on 127.0.0.1:%d", port)

    return runner


def _validate_platega_config() -> bool:
    settings = get_settings()

    has_merchant = bool(settings.PLATEGA_MERCHANT_ID.strip())
    has_secret = bool(settings.PLATEGA_SECRET.strip())

    if has_merchant and not has_secret:
        logger.critical(
            "❌ PLATEGA_MERCHANT_ID задан, но PLATEGA_SECRET пуст! "
            "Webhook будет принимать поддельные запросы. "
            "Укажите PLATEGA_SECRET в .env или удалите PLATEGA_MERCHANT_ID."
        )
        return False

    if has_secret and not has_merchant:
        logger.critical(
            "❌ PLATEGA_SECRET задан, но PLATEGA_MERCHANT_ID пуст! "
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

        if not _validate_platega_config():
            return

        logger.info("Инициализация БД...")
        await init_db()

        logger.info(
            "🔄 Bot started — all in-memory operation locks cleared (restart). "
            "DB constraints + ActionLockMiddleware protect against duplicates."
        )

        bot, dp = await setup_bot()

        webhook_runner = None

        if settings.PLATEGA_MERCHANT_ID and settings.PLATEGA_SECRET:
            webhook_runner = await start_webhook_server(
                settings.PLATEGA_WEBHOOK_PORT
            )

        await resume_pending_broadcasts(bot)
        logger.info("Pending broadcasts resumed (if any)")

        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Received shutdown signal (SIGTERM/SIGINT)")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        await start_background_workers(bot)

        logger.info("Запуск polling...")

        polling_task = asyncio.create_task(dp.start_polling(bot))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [polling_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_event.is_set():
            logger.info("Shutdown requested, stopping polling...")
            await dp.stop_polling()

            polling_task.cancel()

            try:
                await polling_task
            except asyncio.CancelledError:
                pass

        else:
            for task in done:
                exc = task.exception() if not task.cancelled() else None

                if exc:
                    logger.critical(
                        "Fatal error in main task: %s",
                        type(exc).__name__,
                    )

    except Exception as e:
        logger.critical("Fatal error in main: %s", e, exc_info=True)

    finally:
        logger.info("Stopping background workers...")

        try:
            await stop_background_workers()
        except Exception as e:
            logger.error("Error while stopping background workers: %s", e)

        logger.info("Cleaning up resources...")

        if "webhook_runner" in locals() and webhook_runner:
            await webhook_runner.cleanup()

        await close_http_session()
        await close_db()

        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())