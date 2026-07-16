import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, ErrorEvent, MenuButtonCommands
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
)
from config.settings import get_settings
from database.connection import close_db, init_db
from services.amnezia_client import close_http_session
from services.workers import start_background_workers, shutdown_event
from bot.handlers.webhook import setup_webhook_routes

# 🔥 ИСПРАВЛЕНО #10: Формат логов с request_id
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(request_id)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 🔥 ИСПРАВЛЕНО #10: Добавляем CorrelationFilter в корневой logger
root_logger = logging.getLogger()
root_logger.addFilter(CorrelationFilter())
for handler in root_logger.handlers:
    handler.addFilter(CorrelationFilter())

logger = logging.getLogger(__name__)


async def global_error_handler(event: ErrorEvent, **kwargs) -> bool:
    """Глобальный обработчик ошибок с алертом админам"""
    import traceback
    from bot.middlewares.correlation import get_current_request_id

    request_id = get_current_request_id()
    logger.critical(
        "[%s] Unhandled exception: %s",
        request_id,
        event.exception,
        exc_info=event.exception,
    )

    state = kwargs.get("state")
    if state:
        try:
            await state.clear()
        except Exception:
            pass

    try:
        settings = get_settings()
        error_traceback = traceback.format_exc(limit=15)
        error_msg = (
            f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА БОТА</b>\n"
            f"🔍 <b>Request ID:</b> <code>{request_id}</code>\n"
            f"<pre><code>{error_traceback[:3200]}</code></pre>"
        )
        for admin_id in settings.ADMIN_IDS:
            try:
                await event.bot.send_message(admin_id, error_msg, parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        logger.error("[%s] Failed to send error alert: %s", request_id, e)

    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(
                texts.ERROR_TECHNICAL_ALERT, show_alert=True,
            )
        elif event.update.message:
            await event.update.message.answer(
                texts.ERROR_TECHNICAL_MESSAGE, parse_mode="HTML",
            )
    except Exception:
        pass

    return True


async def setup_bot_commands(bot: Bot):
    """Устанавливает меню команд"""
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Bot commands configured")


async def setup_bot() -> tuple[Bot, Dispatcher]:
    """Создаёт и настраивает bot + dispatcher"""
    bot = Bot(token=get_settings().BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # ── Порядок middleware КРИТИЧЕН ──
    # 🔥 ИСПРАВЛЕНО #10: CorrelationMiddleware ПЕРВЫМ
    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())

    # 1. DBSession — нужен всем (создаёт сессию БД)
    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())

    # 2. CleanChat — удаляет сообщения пользователя (SMH)
    dp.message.middleware(CleanChatMiddleware())

    # 3. UserContext — загружает User из БД в data["db_user"]
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

    # 4. Throttling — глобальный rate limit 0.3с + action_type 2.0с
    dp.message.middleware(ThrottlingMiddleware(limit=0.3))
    dp.callback_query.middleware(ThrottlingMiddleware(limit=0.1))

    # 5. ActionLock — эксклюзивная блокировка тяжёлых действий
    dp.callback_query.middleware(ActionLockMiddleware())

    # 6. ChatAction — показывает "typing..." / "uploading..."
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
        start_router, profile_router, connection_router, support_router,
        payment_router, admin_dashboard_router, admin_users_router,
        admin_servers_router, admin_tariffs_router, admin_broadcast_router,
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
    # 🔥 ИСПРАВЛЕНО: Слушаем только localhost, Nginx проксирует сюда
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info("Webhook server started on 127.0.0.1:%d", port)
    return runner


def _validate_platega_config() -> bool:
    """
    🔥 ИСПРАВЛЕНО #14: Проверка конфигурации Platega при старте.
    Если Platega включена (есть merchant_id), но secret пустой —
    webhook будет принимать ЛЮБЫЕ запросы (пустая строка == пустая строка).
    Это критическая уязвимость — бот не должен запускаться.
    """
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
    """Главная функция запуска"""
    settings = get_settings()

    try:
        if not settings.DB_ENCRYPTION_KEY:
            logger.critical("❌ DB_ENCRYPTION_KEY пуст!")
            return

        try:
            Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as e:
            logger.critical("❌ DB_ENCRYPTION_KEY невалиден: %s", e)
            return

        # 🔥 ИСПРАВЛЕНО #14: Проверка конфигурации Platega ДО запуска
        if not _validate_platega_config():
            return

        logger.info("Инициализация БД...")
        await init_db()

        logger.info(
            "🔄 Bot started — all in-memory operation locks cleared (restart). "
            "DB unique constraints + ActionLockMiddleware protect against duplicates."
        )

        bot, dp = await setup_bot()

        webhook_runner = None
        if settings.PLATEGA_MERCHANT_ID and settings.PLATEGA_SECRET:
            webhook_runner = await start_webhook_server(settings.PLATEGA_WEBHOOK_PORT)

        # 🔥 ИСПРАВЛЕНО #5: Graceful shutdown
        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Received shutdown signal (SIGTERM/SIGINT)")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        # Запускаем background workers
        worker_tasks = await start_background_workers(bot)

        logger.info("Запуск polling...")
        polling_task = asyncio.create_task(dp.start_polling(bot))

        # 🔥 ИСПРАВЛЕНО #5: Ждём либо shutdown signal, либо завершение polling
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [polling_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Если shutdown был запрошен — останавливаем polling
        if shutdown_event.is_set():
            logger.info("Shutdown requested, stopping polling...")
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error("Критическая ошибка: %s", e, exc_info=True)

    finally:
        logger.info("Waiting for background workers to finish...")
        if 'worker_tasks' in locals():
            done, pending = await asyncio.wait(
                worker_tasks,
                timeout=10.0,
                return_when=asyncio.ALL_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        logger.info("Cleaning up resources...")
        if 'webhook_runner' in locals() and webhook_runner:
            await webhook_runner.cleanup()
        await close_http_session()
        await close_db()

        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())