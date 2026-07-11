from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _get_tariff_group_name(device_limit: int) -> str:
    """Возвращает человекочитаемое имя группы по device_limit."""
    if device_limit <= 2:
        return "📱 Базовый (2 устр.)"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный (5 устр.)"
    elif device_limit <= 10:
        return f"🚀 Pro ({device_limit} устр.)"
    else:
        return f"🏢 Бизнес ({device_limit} устр.)"


def get_payment_tariff_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # Группируем тарифы по device_limit
    grouped: dict[int, list] = {}
    for tariff in tariffs:
        limit = getattr(tariff, 'device_limit', 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(tariff)

    # Сортируем группы по device_limit
    for limit in sorted(grouped.keys()):
        # Заголовок группы (некликабельная кнопка-разделитель)
        group_name = _get_tariff_group_name(limit)
        builder.button(text=group_name, callback_data="noop_group_header")

        # Тарифы внутри группы (сортировка по duration_days)
        for tariff in sorted(grouped[limit], key=lambda t: t.duration_days):
            device_limit = getattr(tariff, 'device_limit', 2)
            builder.button(
                text=f"⏱ {tariff.duration_days} дн. — {tariff.price_rub}₽ / {tariff.price_stars}⭐",
                callback_data=f"select_tariff:{tariff.id}",
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