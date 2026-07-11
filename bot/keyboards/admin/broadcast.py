from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить всем", callback_data="broadcast_send_all")
    builder.button(text="✅ Только активным", callback_data="broadcast_send_active")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(2, 1)
    return builder.as_markup()