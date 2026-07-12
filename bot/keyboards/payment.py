from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _get_tariff_group_name(device_limit: int) -> str:
    if device_limit <= 2:
        return "📱 Для себя (2 устр.)"
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
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
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
    """Клавиатура продления — БЕЗ кнопки 'Сменить тариф'"""
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽ / {t.price_stars}⭐"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(text=text, callback_data=f"select_tariff:{t.id}")
    # Убрана кнопка "Сменить тариф" — она есть в хабе подписки
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_change_tariff_keyboard(
    tariffs: list, current_limit: int, *, is_subscription_active: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    grouped: dict[int, list] = {}
    for t in tariffs:
        limit = getattr(t, 'device_limit', 2)
        if is_subscription_active and limit < current_limit:
            continue
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(t)

    for limit in sorted(grouped.keys()):
        group_name = _get_tariff_group_name(limit)
        if limit == current_limit:
            group_name += " ✅"
        elif limit > current_limit:
            group_name += " 🔼"
        builder.button(text=group_name, callback_data=f"select_tariff_type:{limit}")
    
    # ИСПРАВЛЕНО: Назад ведет в "Ваша подписка", а не в "Продление"
    builder.button(text="← Назад", callback_data="menu_subscription")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_method_keyboard(tariff_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars:{tariff_id}")
    builder.button(text="🏦 СБП / Карта", callback_data=f"pay_sbp:{tariff_id}")
    # ИСПРАВЛЕНО: Назад ведет обратно к выбору тарифа
    builder.button(text="← Назад", callback_data=f"select_tariff:{tariff_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔌 Подключить устройство", callback_data="menu_connections")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()