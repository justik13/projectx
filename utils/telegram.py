import html
from typing import Optional
from aiogram.exceptions import TelegramBadRequest


def safe(value: Optional[str]) -> str:
    if value is None:
        return '—'
    return html.escape(str(value))


async def safe_edit_text(message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest:
        pass


async def safe_answer(callback, text: str = None, show_alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception:
        pass