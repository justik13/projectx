import logging
from aiogram import BaseMiddleware
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from bot import texts
from config.settings import get_settings
from database.repositories.users_repo import get_user_by_telegram_id

logger = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """
    Проверка бана и подгрузка контекста пользователя.
    🔧 ФИКС: Использует сессию от DBSessionMiddleware (из data['session']),
    а не создаёт свою собственную. Это устраняет утечки сессий и зависания.
    """

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        else:
            user_id = None

        if not user_id:
            return await handler(event, data)

        # 🔧 ФИКС: Берём сессию из DBSessionMiddleware
        session = data.get("session")
        if not session:
            logger.warning("UserContextMiddleware: no session in data, skipping user load")
            return await handler(event, data)

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

        # 🔧 ФИКС: НЕ закрываем сессию здесь — её закроет DBSessionMiddleware
        return await handler(event, data)