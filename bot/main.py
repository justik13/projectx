import asyncio
import logging
from aiogram import Bot, Dispatcher
from config.settings import get_settings

logging.basicConfig(level=logging.INFO)

async def setup_bot():
    settings = get_settings()
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher()
    return bot, dp

async def main():
    bot, dp = await setup_bot()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
