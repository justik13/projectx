import asyncio
import logging
from cachetools import TTLCache
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.chat_action import ChatActionMiddleware
from config.settings import get_settings
from database.connection import init_db, close_db
from services.background_worker import start_background_worker
from bot.middlewares import UserContextMiddleware
from cryptography.fernet import Fernet
from services.amnezia_client import close_http_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class ThrottlingMiddleware:
    def __init__(self, limit: float = 0.5):
        self.limit = limit
        self._last_call = TTLCache(maxsize=10000, ttl=limit * 3)

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id if event.from_user else None
        if hasattr(event, 'data'):
            action_key = event.data
        elif hasattr(event, 'text'):
            action_key = f"msg:{event.text or ''}"
        else:
            action_key = None
        if not user_id or not action_key:
            return await handler(event, data)
        key = f"{user_id}:{action_key}"
        if key in self._last_call:
            if hasattr(event, 'answer'):
                try:
                    await event.answer("⏳ Слишком часто!", show_alert=False)
                except Exception:
                    pass
            return
        self._last_call[key] = asyncio.get_running_loop().time()
        return await handler(event, data)

async def setup_bot() -> tuple[Bot, Dispatcher]:
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    dp.message.middleware(ThrottlingMiddleware(limit=1.0))      # 🔥 P1: Защита от макросов
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

    for r in [start_router, profile_router, connection_router, support_router, payment_router,
              admin_dashboard_router, admin_users_router, admin_servers_router,
              admin_tariffs_router, admin_broadcast_router]:
        dp.include_router(r)

    logger.info("Все роутеры зарегистрированы")
    return bot, dp

async def main():
    try:
        settings = get_settings()
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
        logger.info("БД инициализирована")

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