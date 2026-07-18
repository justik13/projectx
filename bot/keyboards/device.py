from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_device_keyboard(profile_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить имя", callback_data=f"rename_device:{profile_id}")
    builder.button(text="🔑 Показать ключ", callback_data=f"show_config:{profile_id}")
    builder.button(text="📥 Скачать .conf", callback_data=f"download_conf:{profile_id}")
    builder.button(text="🗑 Удалить устройство", callback_data=f"request_delete_device:{profile_id}")
    builder.button(text="← К списку устройств", callback_data="back_to_connections")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1, 1, 1, 2)
    return builder.as_markup()

def get_device_delete_confirm_keyboard(profile_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"confirm_delete_device:{profile_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_delete_device:{profile_id}")
    builder.adjust(2)
    return builder.as_markup()