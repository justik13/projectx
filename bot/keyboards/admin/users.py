from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_user_card_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏰ Выдать доступ", callback_data=f"admin_user_extend:{user_id}")
    builder.button(text="🔧 Управление устройствами", callback_data=f"admin_user_devices:{user_id}")
    builder.button(text="🚫 Забанить / Разбанить", callback_data=f"admin_user_ban:{user_id}")
    builder.button(text="← К списку пользователей", callback_data="admin_users")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_extend_days_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="7 дней", callback_data=f"admin_extend_days:{user_id}:7")
    builder.button(text="30 дней", callback_data=f"admin_extend_days:{user_id}:30")
    builder.button(text="90 дней", callback_data=f"admin_extend_days:{user_id}:90")
    builder.button(text="∞ Навсегда", callback_data=f"admin_extend_days:{user_id}:36500")
    builder.button(text="⌨️ Ввести вручную", callback_data=f"admin_extend_custom:{user_id}")
    builder.button(text="← К карточке пользователя", callback_data=f"admin_user_card:{user_id}")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()