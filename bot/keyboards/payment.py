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


def get_payment_tariff_keyboard(
    tariffs: list, current_tariff_id: int | None = None,
) -> InlineKeyboardMarkup:
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
        group_name = _get_tariff_group_name(limit)
        builder.button(text=group_name, callback_data="noop_group_header")

        for tariff in sorted(grouped[limit], key=lambda t: t.duration_days):
            # Пометка текущего тарифа
            is_current = (current_tariff_id is not None and tariff.id == current_tariff_id)
            badge = " ✅" if is_current else ""

            builder.button(
                text=f"⏱ {tariff.duration_days} дн. — {tariff.price_rub}₽ / {tariff.price_stars}⭐{badge}",
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