from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="🌍 Серверы", callback_data="admin_servers")
    builder.button(text="💰 Тарифы", callback_data="admin_tariffs")
    builder.button(text="📜 Аудит-лог", callback_data="admin_audit")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_audit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin_audit")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()