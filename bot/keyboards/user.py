from aiogram.types import InlineKeyboardMarkup, CopyTextButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_profile_keyboard(is_active: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Пригласить друга", callback_data="referral")
    builder.button(text="🧾 История оплат", callback_data="user_history")
    if is_active:
        builder.button(text="⏳ Моя подписка", callback_data="payment_quick_renew")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_history_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← К профилю", callback_data="back_to_profile")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(2)
    return builder.as_markup()


def get_referral_keyboard(referral_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=referral_link))
    builder.button(text="👥 Список рефералов", callback_data="referrals_list")
    builder.button(text="← К профилю", callback_data="back_to_profile")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1, 2)
    return builder.as_markup()