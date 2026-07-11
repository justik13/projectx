from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def _get_tariff_group_name(device_limit: int) -> str:
    if device_limit <= 2:
        return "📱 Базовый (2 устр.)"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный (5 устр.)"
    elif device_limit <= 10:
        return f"🚀 Pro ({device_limit} устр.)"
    else:
        return f"🏢 Бизнес ({device_limit} устр.)"

def get_tariff_showcase_keyboard(grouped_tariffs: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for limit in sorted(grouped_tariffs.keys()):
        group_name = _get_tariff_group_name(limit)
        builder.button(text=group_name, callback_data=f"select_tariff_type:{limit}")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_tariff_duration_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽ / {t.price_stars}⭐"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(text=text, callback_data=f"select_tariff:{t.id}")
    builder.button(text="← К выбору тарифа", callback_data="payment_showcase")
    builder.adjust(1)
    return builder.as_markup()

def get_renew_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽ / {t.price_stars}⭐"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(text=text, callback_data=f"select_tariff:{t.id}")
    builder.button(text="⚙️ Сменить тариф", callback_data="payment_change_tariff")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_change_tariff_keyboard(tariffs: list, current_limit: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    grouped = {}
    for t in tariffs:
        limit = getattr(t, 'device_limit', 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(t)
    
    for limit in sorted(grouped.keys()):
        group_name = _get_tariff_group_name(limit)
        if limit == current_limit:
            group_name += " ✅"
        elif limit < current_limit:
            group_name += " 🔽"
        else:
            group_name += " 🔼"
        builder.button(text=group_name, callback_data=f"select_tariff_type:{limit}")
    
    builder.button(text="← К продлению", callback_data="payment_renew")
    builder.adjust(1)
    return builder.as_markup()

def get_payment_method_keyboard(tariff_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars:{tariff_id}")
    builder.button(text="🏦 СБП", callback_data=f"pay_sbp:{tariff_id}")
    builder.button(text="← Назад", callback_data="back_to_payment")
    builder.adjust(1)
    return builder.as_markup()