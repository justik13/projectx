from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """
    Middleware для проверки бана и подгрузки контекста пользователя.
    Проверка ToS удалена — использование сервиса означает автоматическое принятие условий.
    """

    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if not user_id:
            return await handler(event, data)

        session = await get_session()
        try:
            user = await get_user_by_telegram_id(session, user_id)
            data['db_user'] = user

            if user and user.is_banned:
                settings = get_settings()
                support_username = settings.SUPPORT_USERNAME.lstrip('@')
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text=f"💬 Связаться с поддержкой @{support_username}",
                        url=f"https://t.me/{support_username}"
                    )]
                ])

                if isinstance(event, Message):
                    await event.answer(
                        "⛔️ У вас заблокирован доступ к сервису.\n"
                        "Если вы считаете, что это ошибка, свяжитесь с поддержкой.",
                        reply_markup=keyboard
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer(
                        "⛔️ У вас заблокирован доступ к сервису.",
                        show_alert=True
                    )
                    try:
                        await event.message.answer(
                            "Если вы считаете, что это ошибка, свяжитесь с поддержкой:",
                            reply_markup=keyboard
                        )
                    except Exception:
                        pass
                return

        except Exception as e:
            logger.error(f"Critical error in UserContextMiddleware: {e}", exc_info=True)
            raise
        finally:
            await session.close()

        return await handler(event, data)