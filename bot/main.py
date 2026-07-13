import asyncio
import logging

from aiogram import Bot, Dispatcher, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, ErrorEvent, MenuButtonCommands
from aiogram.utils.chat_action import ChatActionMiddleware

from bot import texts
from bot.middlewares import DBSessionMiddleware, ThrottlingMiddleware, UserContextMiddleware, CleanChatMiddleware
from bot.handlers import start, profile, payment, connection, support, fallback
from bot.handlers.admin import dashboard, users, servers, tariffs, broadcast
from config.settings import get_settings
from database.connection import init_db, sessionmaker
from services.workers import start_background_worker

logger = logging.getLogger(__name__)
settings = get_settings()

router = Router()

async def set_bot_commands(bot: Bot):
    await bot.set_my_commands(
        [BotCommand(command="start", description="Запустить бота")],
        scope=BotCommandScopeDefault()
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

@router.errors()
async def global_error_handler(event: ErrorEvent, state: FSMContext):
    """
    🔥 ИСПРАВЛЕНО: ErrorEvent не имеет атрибута .data.
    Теперь state пробрасывается через аргументы хендлера aiogram 3.
    """
    logger.critical(f"Unhandled exception: {event.exception}", exc_info=event.exception)
    
    # Очищаем состояние при глобальной ошибке, чтобы избежать "залипания" FSM
    if state:
        await state.clear()
        
    try:
        update = event.update
        chat_id = None
        
        if update.message:
            chat_id = update.message.chat.id
        elif update.callback_query:
            chat_id = update.callback_query.message.chat.id
            # Пытаемся ответить на callback, чтобы убрать "часики" у пользователя
            try:
                await update.callback_query.answer("⚠️ Произошла ошибка. Попробуйте снова.")
            except Exception:
                pass
            
        if chat_id:
            from bot.keyboards import get_back_button
            from utils.telegram import render_hub
            await render_hub(
                event.bot, chat_id,
                "⚠️ <b>Произошла непредвиденная ошибка.</b>\n"
                "Пожалуйста, вернитесь в главное меню и попробуйте снова.",
                get_back_button("back_to_main_menu")
            )
    except Exception as notify_error:
        logger.error(f"Failed to notify user about error: {notify_error}")

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    await init_db()
    
    bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")
    bot.session.timeout = 30
    
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    # Регистрация пользовательских роутеров
    dp.include_router(start.router)
    dp.include_router(profile.router)
    dp.include_router(payment.router)
    dp.include_router(connection.router)
    dp.include_router(support.router)
    
    # Регистрация админских роутеров
    dp.include_router(dashboard.router)
    dp.include_router(users.router)
    dp.include_router(servers.router)
    dp.include_router(tariffs.router)
    dp.include_router(broadcast.router)
    
    # Fallback роутер (должен быть последним)
    dp.include_router(fallback.router)
    
    # Подключение middleware
    dp.update.outer_middleware(DBSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(UserContextMiddleware())
    dp.update.outer_middleware(ThrottlingMiddleware())
    dp.update.outer_middleware(CleanChatMiddleware())
    dp.update.outer_middleware(ChatActionMiddleware())
    
    # Удаление вебхука и установка команд
    await bot.delete_webhook(drop_pending_updates=True)
    await set_bot_commands(bot)
    
    # Запуск фоновых задач (воркеры)
    await start_background_worker(bot)
    
    logger.info("Bot started successfully")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())