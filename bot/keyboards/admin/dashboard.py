from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_menu(
    maintenance_enabled: bool = False,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="👥 Пользователи",
        callback_data="admin_users",
    )

    builder.button(
        text="📢 Рассылка",
        callback_data="admin_broadcast",
    )

    builder.button(
        text="🌍 Серверы",
        callback_data="admin_servers",
    )

    builder.button(
        text="💰 Тарифы",
        callback_data="admin_tariffs",
    )

    builder.button(
        text="📜 Аудит-лог",
        callback_data="admin_audit",
    )

    if maintenance_enabled:
        builder.button(
            text="🛠 Техработы: ВКЛ",
            callback_data="admin_maintenance",
        )
    else:
        builder.button(
            text="🛠 Техработы: ВЫКЛ",
            callback_data="admin_maintenance",
        )

    builder.button(
        text="← В главное меню",
        callback_data="back_to_main_menu",
    )

    builder.adjust(2, 2, 1, 1, 1)

    return builder.as_markup()


def get_audit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🔄 Обновить",
        callback_data="admin_audit",
    )

    builder.button(
        text="← В админку",
        callback_data="admin_menu",
    )

    builder.adjust(1, 1)

    return builder.as_markup()


def get_maintenance_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Подтвердить",
        callback_data="admin_maintenance_toggle_apply",
    )

    builder.button(
        text="❌ Отмена",
        callback_data="admin_menu",
    )

    builder.adjust(2)

    return builder.as_markup()