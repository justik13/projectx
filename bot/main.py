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
    from bot.handlers.profile import router as profile_router  # Добавлено
    dp.include_router(start_router)
    dp.include_router(profile_router)  # Добавлено
    
    logger.info("Routers registered")
    logger.info("Profile router registered")  # Добавлено
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
