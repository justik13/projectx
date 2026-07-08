# bot/main.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import get_settings
from database.connection import init_db, close_db
from services.background_worker import start_background_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def setup_bot() -> tuple[Bot, Dispatcher]:
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрация роутеров
    from bot.handlers.start import router as start_router
    from bot.handlers.profile import router as profile_router
    from bot.handlers.connection import router as connection_router
    from bot.handlers.support import router as support_router
    from bot.handlers.payment import router as payment_router
    from bot.handlers.admin.dashboard import router as admin_dashboard_router
    from bot.handlers.admin.users import router as admin_users_router
    from bot.handlers.admin.servers import router as admin_servers_router
    from bot.handlers.admin.tariffs import router as admin_tariffs_router
    from bot.handlers.admin.broadcast import router as admin_broadcast_router
    
    dp.include_router(start_router)
    dp.include_router(profile_router)
    dp.include_router(connection_router)
    dp.include_router(support_router)
    dp.include_router(payment_router)
    
    dp.include_router(admin_dashboard_router)
    dp.include_router(admin_users_router)
    dp.include_router(admin_servers_router)
    dp.include_router(admin_tariffs_router)
    dp.include_router(admin_broadcast_router)

    logger.info("Все роутеры успешно зарегистрированы")
    return bot, dp


async def main():
    try:
        logger.info("Инициализация базы данных...")
        await init_db()
        logger.info("База данных успешно инициализирована")

        bot, dp = await setup_bot()

        # Запускаем фоновый мониторинг трафика и подписок
        await start_background_worker()

        logger.info("Запуск polling процесса...")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        await close_db()
        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())