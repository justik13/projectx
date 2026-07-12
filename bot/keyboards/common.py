from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from config.settings import get_settings

def get_main_menu(is_admin: bool = False, is_active: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Профиль")
    builder.button(text="🔌 Подключение")
    
    if is_active:
        builder.button(text="⏳ Моя подписка")
    else:
        builder.button(text="🚀 Купить доступ")
        
    builder.button(text="💬 Поддержка")
    
    if is_admin:
        builder.button(text="🛠 Админка")
        builder.adjust(2, 2, 1)
    else:
        builder.adjust(2, 2)
        
    return builder.as_markup(resize_keyboard=True)

def get_back_button(callback_data: str = "back_to_main_menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=callback_data)
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