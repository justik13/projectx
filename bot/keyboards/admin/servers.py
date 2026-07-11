from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_server_card_keyboard(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить", callback_data=f"admin_server_edit:{server_id}")
    status_text = "🔴 Выключить" if is_active else "🟢 Включить"
    builder.button(text=status_text, callback_data=f"admin_server_toggle:{server_id}")
    builder.button(text="🗑 Удалить сервер", callback_data=f"admin_server_delete:{server_id}")
    builder.button(text="← К списку серверов", callback_data="admin_servers")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()