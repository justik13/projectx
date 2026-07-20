from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_tariff_card_keyboard(
    tariff_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✏️ Изменить дни",
        callback_data=f"admin_tariff_edit_days:{tariff_id}",
    )

    builder.button(
        text="✏️ Изменить лимит устр.",
        callback_data=f"admin_tariff_edit_devices:{tariff_id}",
    )

    builder.button(
        text="✏️ Изменить цену ₽",
        callback_data=f"admin_tariff_edit_rub:{tariff_id}",
    )

    builder.button(
        text="✏️ Изменить цену ⭐",
        callback_data=f"admin_tariff_edit_stars:{tariff_id}",
    )

    if is_active:
        status_text = "🔴 Выключить"
    else:
        status_text = "🟢 Включить"

    builder.button(
        text=status_text,
        callback_data=f"admin_tariff_toggle:{tariff_id}",
    )

    builder.button(
        text="🗑 Удалить тариф",
        callback_data=f"admin_tariff_delete:{tariff_id}",
    )

    builder.button(
        text="← К списку тарифов",
        callback_data="admin_tariffs",
    )

    builder.adjust(1, 1, 1, 1, 1, 1, 1)

    return builder.as_markup()