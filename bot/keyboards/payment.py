from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.tariff_names import get_tariff_group_name

def get_tariff_showcase_keyboard(grouped_tariffs: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for limit in sorted(grouped_tariffs.keys()):
        group_name = get_tariff_group_name(limit)
        builder.button(text=group_name, callback_data=f"select_tariff_type:{limit}")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_tariff_duration_keyboard(tariffs: list, *, back_to: str = "payment_showcase") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    tariffs_sorted = sorted(tariffs, key=lambda t: t.duration_days)
    for t in tariffs_sorted:
        text = f"⏱ {t.duration_days} дн. — {t.price_rub}₽ / {t.price_stars}⭐"
        if t.duration_days >= 90:
            text += " 🔥"
        elif t.duration_days >= 30:
            text += " 🌟"
        builder.button(text=text, callback_data=f"select_tariff:{t.id}")
    if back_to == "subscription":
        builder.button(text="← Назад", callback_data="menu_subscription")
    else:
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
    builder.button(text="← Назад", callback_data="menu_subscription")
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
        group_name = get_tariff_group_name(limit)
        if limit == current_limit:
            group_name += " ✅"
        elif limit > current_limit:
            group_name += " 🔼"
        builder.button(text=group_name, callback_data=f"select_tariff_type:{limit}")
    builder.button(text="← Назад", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_payment_method_keyboard(tariff_id: int, device_limit: int | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars:{tariff_id}")
    builder.button(text="🏦 СБП / Карта", callback_data=f"pay_sbp:{tariff_id}")
    if device_limit is not None:
        builder.button(text="← Назад", callback_data=f"select_tariff_type:{device_limit}")
    else:
        builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_payment_success_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔌 Подключить устройство", callback_data="menu_connections")
    builder.button(text="⏳ К подписке", callback_data="menu_subscription")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1, 1)
    return builder.as_markup()

def get_sbp_payment_keyboard(payment_url: str, payment_id: int, tariff_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура для СБП платежа.
    🔥 ИСПРАВЛЕНО: Кнопка "❌ Отменить" теперь ведёт на cancel_invoice,
    чтобы корректно отменить платёж в БД и вернуть пользователя к выбору тарифа.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Открыть страницу оплаты", url=payment_url)
    builder.button(text="✅ Я оплатил (проверить)", callback_data=f"check_payment:{payment_id}")
    # 🔥 ИСПРАВЛЕНО: Используем cancel_invoice для корректной отмены и возврата
    builder.button(text="❌ Отменить", callback_data=f"cancel_invoice:{payment_id}:{tariff_id}")
    builder.adjust(1, 1, 1)
    return builder.as_markup()