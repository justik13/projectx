from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_hub_keyboard(is_admin: bool = False, is_active: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_active:
        builder.button(text="⏳ Моя подписка", callback_data="menu_subscription")
    else:
        builder.button(text="🚀 Купить доступ", callback_data="menu_buy")
    builder.button(text="🔌 Подключения", callback_data="menu_connections")
    builder.button(text="👤 Профиль", callback_data="menu_profile")
    builder.button(text="💬 Поддержка", callback_data="menu_support")
    if is_admin:
        builder.button(text="🛠 Админка", callback_data="menu_admin")
        builder.adjust(1, 2, 2)
    else:
        builder.adjust(1, 2, 1)
    return builder.as_markup()


def get_back_button(callback_data: str = "back_to_main_menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    text = "← В главное меню" if callback_data == "back_to_main_menu" else "← Назад"
    builder.button(text=text, callback_data=callback_data)
    return builder.as_markup()