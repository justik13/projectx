import asyncio
import logging
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
)
from config.settings import get_settings
from database.connection import close_db, init_db
from services.amnezia_client import close_http_session
from services.workers import start_background_worker
from bot.handlers.webhook import setup_webhook_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def global_error_handler(event: ErrorEvent, **kwargs) -> bool:
    """Глобальный обработчик ошибок с алертом админам"""
    import traceback
    logger.critical("Unhandled exception: %s", event.exception, exc_info=event.exception)

    state = kwargs.get("state")
    if state:
        try:
            await state.clear()
        except Exception:
            pass

    # 🔥 НОВОЕ: Отправка traceback админам
    try:
        settings = get_settings()
        error_traceback = traceback.format_exc(limit=15)
        error_msg = (
            f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА БОТА</b>\n"
            f"<pre><code>{error_traceback[:3500]}</code></pre>"
        )
        for admin_id in settings.ADMIN_IDS:
            try:
                await event.bot.send_message(admin_id, error_msg, parse_mode="HTML")
            except Exception:
                pass
    except Exception as e:
        logger.error("Failed to send error alert: %s", e)

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

    # 5. ActionLock — эксклюзивная блокировка тяжёлых действий (НОВОЕ)
    # Ставится ПОСЛЕ Throttling: если throttling отсёк запрос,
    # action lock не тратит ресурсы на проверку
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

        logger.info("Инициализация БД...")
        await init_db()

        # 🔥 НОВОЕ: Логирование при старте бота (Проблема 6 — In-Memory State)
        # Все in-memory блокировки (_creating_devices, _deleting_devices,
        # _processing_payments и т.д.) инициализируются пустыми при каждом старте.
        # Для single-worker это acceptable risk:
        # - DB unique constraint на peer_id защищает от дубликатов устройств
        # - ThrottlingMiddleware защищает от double-click
        # - ActionLockMiddleware защищает от параллельных тяжёлых действий
        logger.info(
            "🔄 Bot started — all in-memory operation locks cleared (restart). "
            "DB unique constraints + ActionLockMiddleware protect against duplicates."
        )

        bot, dp = await setup_bot()

        webhook_runner = None
        if settings.PLATEGA_MERCHANT_ID and settings.PLATEGA_SECRET:
            webhook_runner = await start_webhook_server(settings.PLATEGA_WEBHOOK_PORT)

        await start_background_worker(bot)

        logger.info("Запуск polling...")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error("Критическая ошибка: %s", e, exc_info=True)
    finally:
        if 'webhook_runner' in locals() and webhook_runner:
            await webhook_runner.cleanup()
        await close_db()
        await close_http_session()
        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())