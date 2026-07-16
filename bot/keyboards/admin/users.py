from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.tariff_names import get_tariff_group_name


def get_admin_user_card_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Подписка", callback_data=f"admin_subscription:{user_id}")
    builder.button(text="🔧 Устройства", callback_data=f"admin_user_devices:{user_id}")
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


# ═══════════════════════════════════════════════════════════
# 🔧 НОВОЕ: Управление подпиской
# ═══════════════════════════════════════════════════════════

def get_admin_subscription_keyboard(
    telegram_id: int,
    has_active_sub: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if has_active_sub:
        builder.button(text="💎 Сменить тариф", callback_data=f"admin_sub_change_tariff:{telegram_id}")
        builder.button(text="➕ Продлить доступ", callback_data=f"admin_sub_extend:{telegram_id}")
        builder.button(text="➖ Уменьшить дни", callback_data=f"admin_sub_reduce:{telegram_id}")
    else:
        builder.button(text="🎫 Выдать доступ", callback_data=f"admin_sub_grant:{telegram_id}")
    builder.button(text="← К карточке", callback_data=f"admin_user_card:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_change_tariff_keyboard(
    telegram_id: int,
    tariffs: list,
    current_tariff_id: int | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if not getattr(tariff, "is_active", True):
            continue
        label = get_tariff_group_name(tariff.device_limit)
        if tariff.id == current_tariff_id:
            label += " ✅"
        builder.button(
            text=label,
            callback_data=f"admin_sub_select_tariff:{telegram_id}:{tariff.id}",
        )
    builder.button(text="← Назад", callback_data=f"admin_subscription:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_grant_tariff_keyboard(
    telegram_id: int,
    tariffs: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        if not getattr(tariff, "is_active", True):
            continue
        label = get_tariff_group_name(tariff.device_limit)
        builder.button(
            text=label,
            callback_data=f"admin_sub_grant_tariff:{telegram_id}:{tariff.id}",
        )
    builder.button(text="← Назад", callback_data=f"admin_subscription:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_grant_days_keyboard(
    telegram_id: int,
    tariff_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for days in (7, 30, 90):
        builder.button(
            text=f"{days} дней",
            callback_data=f"admin_sub_grant_confirm:{telegram_id}:{tariff_id}:{days}",
        )
    builder.button(
        text="∞ Навсегда",
        callback_data=f"admin_sub_grant_confirm:{telegram_id}:{tariff_id}:36500",
    )
    builder.button(
        text="⌨️ Ввести вручную",
        callback_data=f"admin_sub_grant_custom:{telegram_id}:{tariff_id}",
    )
    builder.button(
        text="← Назад",
        callback_data=f"admin_sub_grant:{telegram_id}",
    )
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_extend_days_new_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for days in (7, 30, 90):
        builder.button(
            text=f"{days} дней",
            callback_data=f"admin_sub_confirm_extend:{telegram_id}:{days}",
        )
    builder.button(
        text="∞ Навсегда",
        callback_data=f"admin_sub_confirm_extend:{telegram_id}:36500",
    )
    builder.button(
        text="⌨️ Ввести вручную",
        callback_data=f"admin_sub_extend_custom:{telegram_id}",
    )
    builder.button(
        text="← Назад",
        callback_data=f"admin_subscription:{telegram_id}",
    )
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_confirm_action_keyboard(
    confirm_callback: str,
    cancel_callback: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=confirm_callback)
    builder.button(text="❌ Отмена", callback_data=cancel_callback)
    builder.adjust(2)
    return builder.as_markup()


def get_admin_user_devices_keyboard(
    telegram_id: int,
    profiles: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for profile in profiles:
        name = getattr(profile, "device_name", None) or f"Устройство #{profile.id}"
        builder.button(
            text=f"🗑 {name}",
            callback_data=f"admin_delete_device:{telegram_id}:{profile.id}",
        )
    builder.button(text="← К карточке", callback_data=f"admin_user_card:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_back_button(callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()