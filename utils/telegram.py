# utils/telegram.py — безопасные обёртки над Telegram API и санитизация ввода

import html
from typing import Optional

from aiogram.exceptions import TelegramBadRequest, TelegramAPIError


def safe(value: Optional[str]) -> str:
    """
    Единая точка санитизации пользовательского ввода для HTML-режима.

    - Если value == None → возвращает прочерк «—»
    - Иначе применяет html.escape для защиты от инъекций

    Заменяет все прямые вызовы html.escape() по проекту.
    """
    if value is None:
        return "—"
    return html.escape(str(value))


async def safe_edit_text(message, text: str, **kwargs) -> bool:
    """Попытка отредактировать сообщение. Возвращает True при успехе."""
    try:
        await message.edit_text(text, **kwargs)
        return True
    except (TelegramBadRequest, TelegramAPIError, Exception):
        return False


async def safe_delete_message(message) -> bool:
    """
    Безопасное удаление сообщения.

    Подавляет любые исключения (сообщение могло быть удалено ранее,
    бот мог потерять права или превысить таймаут).

    Заменяет все паттерны вида:
        try:
            await message.delete()
        except Exception:
            pass
    """
    try:
        await message.delete()
        return True
    except Exception:
        return False


async def safe_answer(callback, text: Optional[str] = None, show_alert: bool = False) -> bool:
    """Попытка ответить на callback. Возвращает True при успехе."""
    try:
        await callback.answer(text, show_alert=show_alert)
        return True
    except Exception:
        return False