# bot/keyboards.py — все клавиатуры бота
from aiogram.types import (
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CopyTextButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def get_main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Профиль")
    builder.button(text="🔌 Подключение")
    builder.button(text="💳 Оплата")
    builder.button(text="💬 Поддержка")
    if is_admin:
        builder.button(text="🛠 Админка")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


def get_help_keyboard() -> InlineKeyboardMarkup:
    from config.settings import get_settings
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="📖 Пользовательское соглашение", url="https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19")
    builder.button(text="🔒 Политика конфиденциальности", url="https://telegra.ph/Politika-konfidencialnosti-04-01-26")
    builder.adjust(1)
    return builder.as_markup()


def get_profile_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Пригласить друга", callback_data="referral")
    builder.button(text="🧾 История оплат", callback_data="user_history")
    builder.adjust(1)
    return builder.as_markup()


def get_history_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← К профилю", callback_data="back_to_profile")
    return builder.as_markup()


def get_referral_keyboard(referral_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📋 Скопировать ссылку",
        copy_text=CopyTextButton(text=referral_link)
    )
    builder.button(text="👥 Список рефералов", callback_data="referrals_list")
    builder.button(text="← К профилю", callback_data="back_to_profile")
    builder.adjust(1)
    return builder.as_markup()


def get_connection_keyboard(has_subscription: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_subscription:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")
    builder.adjust(1)
    return builder.as_markup()


def get_device_keyboard(profile_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить имя", callback_data=f"rename_device:{profile_id}")
    builder.button(text="🔑 Показать ключ", callback_data=f"show_config:{profile_id}")
    builder.button(text="📥 Скачать .conf", callback_data=f"download_conf:{profile_id}")
    builder.button(text="🗑 Удалить устройство", callback_data=f"request_delete_device:{profile_id}")
    builder.button(text="← К списку устройств", callback_data="back_to_connections")
    builder.adjust(1)
    return builder.as_markup()


def get_device_delete_confirm_keyboard(profile_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"confirm_delete_device:{profile_id}")
    builder.button(text="❌ Отмена", callback_data=f"cancel_delete_device:{profile_id}")
    builder.adjust(2)
    return builder.as_markup()


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


def get_payment_confirm_keyboard(tariff_id: int, amount: int, currency: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if currency == "stars":
        builder.button(text=f"💎 Оплатить {amount} ⭐", callback_data=f"confirm_payment:{tariff_id}")
    else:
        builder.button(text=f"💎 Оплатить {amount} ₽", callback_data=f"confirm_payment:{tariff_id}")
    builder.button(text="← К выбору способа оплаты", callback_data="back_to_payment_method")
    builder.adjust(1)
    return builder.as_markup()


def get_support_keyboard(support_username: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать в поддержку", url=f"https://t.me/{support_username.lstrip('@')}")
    return builder.as_markup()


def get_back_button(callback_data: str = "back_to_main_menu") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=callback_data)
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════
# АДМИН-КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════

def get_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="🌍 Серверы", callback_data="admin_servers")
    builder.button(text="💰 Тарифы", callback_data="admin_tariffs")
    builder.button(text="📜 Аудит-лог", callback_data="admin_audit")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_user_card_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏰ Выдать доступ", callback_data=f"admin_user_extend:{user_id}")
    builder.button(text="🔧 Управление устройствами", callback_data=f"admin_user_devices:{user_id}")
    builder.button(text="🚫 Забанить / Разбанить", callback_data=f"admin_user_ban:{user_id}")
    builder.button(text="← К списку пользователей", callback_data="admin_users")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_extend_days_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="7 дней", callback_data=f"admin_extend_days:{user_id}:7")
    builder.button(text="30 дней", callback_data=f"admin_extend_days:{user_id}:30")
    builder.button(text="90 дней", callback_data=f"admin_extend_days:{user_id}:90")
    builder.button(text="∞ Навсегда", callback_data=f"admin_extend_days:{user_id}:36500")
    builder.button(text="⌨️ Ввести вручную", callback_data=f"admin_extend_custom:{user_id}")
    builder.button(text="← К карточке пользователя", callback_data=f"admin_user_card:{user_id}")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_servers_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить сервер", callback_data="admin_server_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


def get_admin_server_card_keyboard(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить", callback_data=f"admin_server_edit:{server_id}")
    status_text = "🔴 Выключить" if is_active else "🟢 Включить"
    builder.button(text=status_text, callback_data=f"admin_server_toggle:{server_id}")
    builder.button(text="🗑 Удалить сервер", callback_data=f"admin_server_delete:{server_id}")
    builder.button(text="← К списку серверов", callback_data="admin_servers")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def get_admin_tariffs_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить тариф", callback_data="admin_tariff_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


def get_admin_tariff_card_keyboard(tariff_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить дни", callback_data=f"admin_tariff_edit_days:{tariff_id}")
    builder.button(text="✏️ Изменить цену ₽", callback_data=f"admin_tariff_edit_rub:{tariff_id}")
    builder.button(text="✏️ Изменить цену ⭐", callback_data=f"admin_tariff_edit_stars:{tariff_id}")
    status_text = "🔴 Выключить" if is_active else "🟢 Включить"
    builder.button(text=status_text, callback_data=f"admin_tariff_toggle:{tariff_id}")
    builder.button(text="🗑 Удалить тариф", callback_data=f"admin_tariff_delete:{tariff_id}")
    builder.button(text="← К списку тарифов", callback_data="admin_tariffs")
    builder.adjust(1, 1, 1, 1, 1, 1)
    return builder.as_markup()


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить всем", callback_data="broadcast_send_all")
    builder.button(text="✅ Только активным", callback_data="broadcast_send_active")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(2, 1)
    return builder.as_markup()


def get_audit_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="admin_audit")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()