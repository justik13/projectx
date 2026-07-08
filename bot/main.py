# bot/main.py — точка входа бота
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import get_settings
from database.connection import init_db, close_db

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def setup_bot() -> tuple[Bot, Dispatcher]:
    """Инициализация бота и диспетчера"""
    settings = get_settings()

    # Создаём бота
    bot = Bot(token=settings.BOT_TOKEN)

    # Создаём диспетчер с FSM-хранилищем в памяти
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Регистрируем роутеры
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
    
    # Пользовательские роутеры
    dp.include_router(start_router)
    dp.include_router(profile_router)
    dp.include_router(connection_router)
    dp.include_router(support_router)
    dp.include_router(payment_router)
    
    # Админ-роутеры
    dp.include_router(admin_dashboard_router)
    dp.include_router(admin_users_router)
    dp.include_router(admin_servers_router)
    dp.include_router(admin_tariffs_router)
    dp.include_router(admin_broadcast_router)

    logger.info("Routers registered")
    logger.info("Profile router registered")
    logger.info("Connection router registered")
    logger.info("Support router registered")
    logger.info("Payment router registered")
    logger.info("Admin routers registered")
    return bot, dp


async def main():
    """Главная функция запуска бота"""
    try:
        # Инициализируем базу данных
        logger.info("Initializing database...")
        await init_db()
        logger.info("Database initialized")

        # Настраиваем бота
        bot, dp = await setup_bot()

        # Запускаем polling
        logger.info("Starting polling...")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"Error during bot startup: {e}", exc_info=True)
    finally:
        # Закрываем соединение с БД
        await close_db()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())