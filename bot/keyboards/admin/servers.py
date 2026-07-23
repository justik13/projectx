from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_server_card_keyboard(
    server_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✏️ Изменить имя",
        callback_data=f"admin_server_edit_name:{server_id}",
    )
    builder.button(
        text="🏳 Изменить флаг",
        callback_data=f"admin_server_edit_flag:{server_id}",
    )
    builder.button(
        text="🔗 Изменить URL",
        callback_data=f"admin_server_edit_url:{server_id}",
    )
    builder.button(
        text="🔑 Изменить ключ",
        callback_data=f"admin_server_edit_key:{server_id}",
    )
    builder.button(
        text="👥 Изменить лимит",
        callback_data=f"admin_server_edit_max_clients:{server_id}",
    )

    if is_active:
        status_text = "🔴 Выключить"
    else:
        status_text = "🟢 Включить"

    builder.button(
        text=status_text,
        callback_data=f"admin_server_toggle:{server_id}",
    )
    builder.button(
        text="🗑 Удалить сервер",
        callback_data=f"admin_server_delete:{server_id}",
    )
    builder.button(
        text="← К списку серверов",
        callback_data="admin_servers",
    )

    builder.adjust(1, 1, 1, 1, 1, 1, 1, 1)
    return builder.as_markup()


def get_server_delete_confirm_keyboard(
    server_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Да, удалить полностью",
        callback_data=f"confirm_server_delete:{server_id}",
    )
    builder.button(
        text="❌ Отмена",
        callback_data=f"admin_server_card:{server_id}",
    )
    builder.button(
        text="← К списку серверов",
        callback_data="admin_servers",
    )
    builder.button(
        text="🏠 В главное меню",
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()