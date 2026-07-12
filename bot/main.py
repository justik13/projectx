import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, ErrorEvent, MenuButtonCommands
from aiogram.utils.chat_action import ChatActionMiddleware
from cryptography.fernet import Fernet
from bot import texts
from bot.middlewares import DBSessionMiddleware, ThrottlingMiddleware, UserContextMiddleware, CleanChatMiddleware
from config.settings import get_settings
from database.connection import close_db, init_db
from services.amnezia_client import close_http_session
from services.workers import start_background_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def global_error_handler(event: ErrorEvent) -> bool:
    logger.critical(f"Unhandled exception: {event.exception}", exc_info=True)
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
    commands = [
        BotCommand(command="start", description="🚀 Запустить бота"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logger.info("Bot commands configured")


async def setup_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=get_settings().BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок middleware важен!
    # 1. DB Session — создает сессию
    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())
    
    # 2. Clean Chat — удаляет входящие сообщения (ДО UserContext)
    dp.message.middleware(CleanChatMiddleware())
    
    # 3. User Context — загружает пользователя
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    
    # 4. Throttling — защита от спама
    dp.message.middleware(ThrottlingMiddleware(limit=0.3))
    dp.callback_query.middleware(ThrottlingMiddleware(limit=0.1))
    
    # 5. Chat Action — показывает "печатает..."
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


async def main():
    settings = get_settings()
    try:
        if not settings.DB_ENCRYPTION_KEY:
            logger.critical("❌ DB_ENCRYPTION_KEY пуст!")
            return
        try:
            Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as e:
            logger.critical(f"❌ DB_ENCRYPTION_KEY невалиден: {e}")
            return

        logger.info("Инициализация БД...")
        await init_db()

        bot, dp = await setup_bot()
        await start_background_worker(bot)

        logger.info("Запуск polling...")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        await close_db()
        await close_http_session()
        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())