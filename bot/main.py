# bot/main.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.chat_action import ChatActionMiddleware
from config.settings import get_settings
from database.connection import init_db, close_db
from services.background_worker import start_background_worker
from bot.middlewares import UserContextMiddleware
from cryptography.fernet import Fernet
from services.amnezia_client import close_http_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# 🔥 FIX P2: Throttling Middleware для защиты от спама кнопками
class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.5):
        self.limit = limit
        self._last_call = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        callback_data = event.data if hasattr(event, 'data') else None
        
        if not user_id or not callback_data:
            return await handler(event, data)
        
        key = f"{user_id}:{callback_data}"
        now = asyncio.get_running_loop().time()
        
        last_time = self._last_call.get(key, 0)
        if now - last_time < self.limit:
            if hasattr(event, 'answer'):
                try:
                    await event.answer("⏳ Слишком часто! Подождите секунду.", show_alert=False)
                except Exception:
                    pass
            return
        
        self._last_call[key] = now
        
        if len(self._last_call) > 10000:
            self._last_call = {k: v for k, v in self._last_call.items() if now - v < self.limit * 2}
        
        return await handler(event, data)


async def setup_bot() -> tuple[Bot, Dispatcher]:
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    
    # 🔥 FIX P2: Антиспам на инлайн-кнопки
    dp.callback_query.middleware(ThrottlingMiddleware(limit=0.5))
    
    dp.message.middleware(ChatActionMiddleware())

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
        settings = get_settings()
        if not settings.DB_ENCRYPTION_KEY:
            logger.critical("❌ КРИТИЧЕСКАЯ ОШИБКА: Переменная DB_ENCRYPTION_KEY пуста или отсутствует в .env!")
            return
        try:
            Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
        except (ValueError, Exception) as e:
            logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: DB_ENCRYPTION_KEY невалиден: {e}")
            return

        logger.info("Инициализация базы данных...")
        await init_db()
        logger.info("База данных успешно инициализирована")

        bot, dp = await setup_bot()
        await start_background_worker()

        logger.info("Запуск polling процесса...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        await close_db()
        await close_http_session()
        logger.info("Работа бота завершена")

if __name__ == "__main__":
    asyncio.run(main())