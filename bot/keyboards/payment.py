from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_payment_tariff_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(
            text=f"⏱ {tariff.duration_days} дней — {tariff.price_rub} ₽ / {tariff.price_stars} ⭐",
            callback_data=f"select_tariff:{tariff.id}"
        )
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_method_keyboard(tariff_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars:{tariff_id}")
    builder.button(text="🏦 СБП", callback_data=f"pay_sbp:{tariff_id}")
    builder.button(text="← К выбору тарифа", callback_data="back_to_payment")
    builder.adjust(1)
    return builder.as_markup()