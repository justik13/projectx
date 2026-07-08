# bot/keyboards.py — все клавиатуры бота

from aiogram.types import (
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder


def get_main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню (Reply-клавиатура)"""
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Профиль")
    builder.button(text="🔌 Подключение")
    builder.button(text="💳 Оплата")
    builder.button(text="💬 Поддержка")
    if is_admin:
        builder.button(text="🛠 Админка")
    builder.adjust(2, 2)
    return builder.as_markup(resize_keyboard=True)


def get_tos_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для принятия оферты"""
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Читать оферту", callback_data="read_tos")
    builder.button(text="✅ Принять", callback_data="accept_tos")
    return builder.as_markup()


def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для раздела Профиль"""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Пригласить друга", callback_data="referral")
    return builder.as_markup()


def get_referral_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для раздела Рефералы"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Список рефералов", callback_data="referrals_list")
    builder.button(text="← Назад", callback_data="back_to_profile")
    builder.adjust(1)
    return builder.as_markup()


def get_connection_keyboard(has_subscription: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура для раздела Подключение"""
    builder = InlineKeyboardBuilder()
    if has_subscription:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")
    builder.button(text="← Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def get_device_keyboard(profile_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для управления устройством"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить имя", callback_data=f"rename_device:{profile_id}")
    builder.button(text="📥 Скачать .conf", callback_data=f"download_conf:{profile_id}")
    builder.button(text="🗑 Удалить устройство", callback_data=f"delete_device:{profile_id}")
    builder.button(text="← Назад", callback_data="back_to_connections")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_tariff_keyboard(tariffs: list) -> InlineKeyboardMarkup:
    """Клавиатура для выбора тарифа"""
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(
            text=f"⏱ {tariff.duration_days} дней — {tariff.price_rub} ₽ / {tariff.price_stars} ⭐",
            callback_data=f"select_tariff:{tariff.id}"
        )
    builder.button(text="← Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_method_keyboard(tariff_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора способа оплаты"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Telegram Stars", callback_data=f"pay_stars:{tariff_id}")
    builder.button(text="🏦 СБП", callback_data=f"pay_sbp:{tariff_id}")
    builder.button(text="← Назад", callback_data="back_to_payment")
    builder.adjust(1)
    return builder.as_markup()


def get_payment_confirm_keyboard(payment_id: int, amount: int, currency: str) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения оплаты"""
    builder = InlineKeyboardBuilder()
    if currency == "stars":
        builder.button(text=f"💎 Оплатить {amount} ⭐", callback_data=f"confirm_payment:{payment_id}")
    else:
        builder.button(text=f"💎 Оплатить {amount} ₽", callback_data=f"confirm_payment:{payment_id}")
    builder.button(text="← Назад", callback_data="back_to_payment_method")
    builder.adjust(1)
    return builder.as_markup()


def get_support_keyboard(support_username: str) -> InlineKeyboardMarkup:
    """Клавиатура для раздела Поддержка"""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать в поддержку", url=f"https://t.me/{support_username.lstrip('@')}")
    return builder.as_markup()


def get_back_button(callback_data: str = "back_to_main") -> InlineKeyboardMarkup:
    """Универсальная кнопка Назад"""
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=callback_data)
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════════
# АДМИН-КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════

def get_admin_menu() -> InlineKeyboardMarkup:
    """Главное меню админки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Пользователи", callback_data="admin_users")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast")
    builder.button(text="🌍 Серверы", callback_data="admin_servers")
    builder.button(text="💰 Тарифы", callback_data="admin_tariffs")
    builder.button(text="← Назад", callback_data="back_to_main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_admin_users_keyboard(page: int = 1, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Клавиатура для списка пользователей (пагинация)"""
    builder = InlineKeyboardBuilder()
    if page > 1:
        builder.button(text="⬅️ Назад", callback_data=f"admin_users_page:{page - 1}")
    if page < total_pages:
        builder.button(text="Вперёд ➡️", callback_data=f"admin_users_page:{page + 1}")
    builder.button(text="🔍 Поиск по ID", callback_data="admin_users_search")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def get_admin_user_card_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для карточки пользователя"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⏰ Выдать доступ", callback_data=f"admin_user_extend:{user_id}")
    builder.button(text="🔧 Управление устройствами", callback_data=f"admin_user_devices:{user_id}")
    builder.button(text="🚫 Забанить / Разбанить", callback_data=f"admin_user_ban:{user_id}")
    builder.button(text="← К списку", callback_data="admin_users")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_extend_days_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора дней продления"""
    builder = InlineKeyboardBuilder()
    builder.button(text="7 дней", callback_data=f"admin_extend_days:{user_id}:7")
    builder.button(text="30 дней", callback_data=f"admin_extend_days:{user_id}:30")
    builder.button(text="90 дней", callback_data=f"admin_extend_days:{user_id}:90")
    builder.button(text="∞ Навсегда", callback_data=f"admin_extend_days:{user_id}:36500")
    builder.button(text="⌨️ Ввести вручную", callback_data=f"admin_extend_custom:{user_id}")
    builder.button(text="← Назад", callback_data=f"admin_user_card:{user_id}")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_servers_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для списка серверов"""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить сервер", callback_data="admin_server_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


def get_admin_server_card_keyboard(server_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура для карточки сервера"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить", callback_data=f"admin_server_edit:{server_id}")
    status_text = "🔴 Выключить" if is_active else "🟢 Включить"
    builder.button(text=status_text, callback_data=f"admin_server_toggle:{server_id}")
    builder.button(text="🗑 Удалить сервер", callback_data=f"admin_server_delete:{server_id}")
    builder.button(text="← К серверам", callback_data="admin_servers")
    builder.adjust(1, 1, 1, 1)
    return builder.as_markup()


def get_admin_tariffs_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для списка тарифов"""
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить тариф", callback_data="admin_tariff_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1, 1)
    return builder.as_markup()


def get_admin_tariff_card_keyboard(tariff_id: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура для карточки тарифа"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить дни", callback_data=f"admin_tariff_edit_days:{tariff_id}")
    builder.button(text="✏️ Изменить цену ₽", callback_data=f"admin_tariff_edit_rub:{tariff_id}")
    builder.button(text="✏️ Изменить цену ⭐", callback_data=f"admin_tariff_edit_stars:{tariff_id}")
    status_text = "🔴 Выключить" if is_active else "🟢 Включить"
    builder.button(text=status_text, callback_data=f"admin_tariff_toggle:{tariff_id}")
    builder.button(text="🗑 Удалить тариф", callback_data=f"admin_tariff_delete:{tariff_id}")
    builder.button(text="← К тарифам", callback_data="admin_tariffs")
    builder.adjust(1, 1, 1, 1, 1, 1)
    return builder.as_markup()


def get_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения рассылки"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Отправить всем", callback_data="broadcast_send_all")
    builder.button(text="✅ Только активным", callback_data="broadcast_send_active")
    builder.button(text="❌ Отмена", callback_data="admin_menu")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_tos_accept_keyboard():
    """Клавиатура для принятия оферты с 3 кнопками"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.texts import TOS_AGREEMENT_URL, PRIVACY_POLICY_URL
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📖 Пользовательское соглашение", url=TOS_AGREEMENT_URL)
    builder.button(text="🔒 Политика конфиденциальности", url=PRIVACY_POLICY_URL)
    builder.button(text="✅ Принять", callback_data="accept_tos")
    builder.adjust(1)
    return builder.as_markup()