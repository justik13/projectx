from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config.settings import get_settings

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
    builder.button(text="← В главное меню", callback_data=callback_data)
    return builder.as_markup()

def get_help_keyboard() -> InlineKeyboardMarkup:
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="📖 Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19")
    builder.button(text="🔒 Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-04-01-26")
    builder.adjust(1)
    return builder.as_markup()