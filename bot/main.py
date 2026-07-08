# bot/main.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import get_settings
from database.connection import init_db, close_db, get_session
from services.background_worker import start_background_worker
from bot.middlewares import UserContextMiddleware
from cryptography.fernet import Fernet
from datetime import datetime
from database.repositories.users_repo import get_all_users, get_user_by_telegram_id
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.servers_repo import get_server_by_id
from services.amnezia_client import AmneziaClient

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

    # Регистрируем middleware глобально
    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

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


async def revoke_expired_subscriptions():
    """Фоновая задача для автоматического отзыва истекших подписок"""
    while True:
        try:
            logger.info("Проверка истекших подписок...")
            
            # Получаем все пользователей с истекшей подпиской
            session = await get_session()
            expired_users = await get_all_users(session)
            
            for user in expired_users:
                if user.subscription_end and user.subscription_end < datetime.utcnow():
                    logger.info(f"Найден истекший пользователь: {user.telegram_id}")
                    
                    # Получаем активные профили пользователя
                    profiles = await get_user_profiles(session, user.id)
                    
                    for profile in profiles:
                        try:
                            # Получаем информацию о сервере
                            server = await get_server_by_id(session, profile.server_id)
                            
                            if server:
                                # Создаем клиент для Amnezia API
                                client = AmneziaClient(server.api_url, server.api_key)
                                
                                # Отключаем клиента на сервере
                                result = await client.update_client(
                                    client_id=profile.peer_id,
                                    protocol=server.protocol,
                                    status="disabled"
                                )
                                
                                if result:
                                    logger.info(f"Отключен клиент {profile.peer_id} на сервере {server.name}")
                                else:
                                    logger.warning(f"Не удалось отключить клиента {profile.peer_id} на сервере {server.name}")
                            else:
                                logger.warning(f"Сервер не найден для профиля {profile.id}")
                                
                        except Exception as e:
                            logger.error(f"Ошибка при обработке профиля {profile.id}: {e}", exc_info=True)
            
            await session.close()
            
        except Exception as e:
            logger.error(f"Ошибка в фоновой задаче проверки подписок: {e}", exc_info=True)
        
        # Ждем 30 минут перед следующей проверкой
        await asyncio.sleep(1800)


async def main():
    try:
        settings = get_settings()
        
        # Проверка ключа шифрования
        if settings.DB_ENCRYPTION_KEY:
            try:
                Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
            except (ValueError, Exception) as e:
                logger.critical(f"❌ DB_ENCRYPTION_KEY is invalid: {e}")
                logger.critical("Generate a valid key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
                return

        logger.info("Инициализация базы данных...")
        await init_db()
        logger.info("База данных успешно инициализирована")

        bot, dp = await setup_bot()

        # Запускаем фоновый мониторинг трафика и подписок
        await start_background_worker()

        # Запускаем фоновую задачу для отзывания истекших подписок
        asyncio.create_task(revoke_expired_subscriptions())

        logger.info("Запуск polling процесса...")
        await dp.start_polling(bot)

    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        await close_db()
        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())
