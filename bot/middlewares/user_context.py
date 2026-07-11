import logging

from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from bot import texts
from config.settings import get_settings
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id

logger = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """Проверка бана и подгрузка контекста пользователя."""

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        else:
            user_id = None

        if not user_id:
            return await handler(event, data)

        session = await get_session()
        try:
            user = await get_user_by_telegram_id(session, user_id)
            data["db_user"] = user

            if user and user.is_banned:
                support_username = get_settings().SUPPORT_USERNAME.lstrip("@")
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(
                        text=f"💬 Связаться с поддержкой @{support_username}",
                        url=f"https://t.me/{support_username}",
                    )]],
                )

                if isinstance(event, Message):
                    await event.answer(
                        texts.ERROR_BANNED_MESSAGE, reply_markup=keyboard,
                    )
                elif isinstance(event, CallbackQuery):
                    await event.answer(texts.ERROR_BANNED_ALERT, show_alert=True)
                    try:
                        await event.message.answer(
                            "Если вы считаете, что это ошибка, свяжитесь с поддержкой:",
                            reply_markup=keyboard,
                        )
                    except Exception:
                        pass

                return  # обрываем цепочку обработки

        except Exception as e:
            logger.error(f"Critical error in UserContextMiddleware: {e}", exc_info=True)
            raise
        finally:
            await session.close()

        return await handler(event, data)