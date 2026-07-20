from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.tariff_names import get_tariff_group_name


# ═══════════════════════════════════════════════════════════
# 👤 КАРТОЧКА ПОЛЬЗОВАТЕЛЯ (с динамической кнопкой бана)
# ═══════════════════════════════════════════════════════════


def get_admin_user_card_keyboard(
    user_id: int,
    is_banned: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text="📅 Подписка",
        callback_data=f"admin_subscription:{user_id}",
    )

    builder.button(
        text="🔧 Устройства",
        callback_data=f"admin_user_devices:{user_id}",
    )

    if is_banned:
        builder.button(
            text="✅ Разбанить",
            callback_data=f"admin_unban:{user_id}",
        )
    else:
        builder.button(
            text="🚫 Забанить",
            callback_data=f"admin_ban:{user_id}",
        )

    builder.button(
        text="← К списку пользователей",
        callback_data="admin_users",
    )

    builder.adjust(1)

    return builder.as_markup()


# ═══════════════════════════════════════════════════════════
# 📅 ПОДПИСКА
# ═══════════════════════════════════════════════════════════


def get_admin_subscription_keyboard(
    telegram_id: int,
    has_active_sub: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if has_active_sub:
        builder.button(
            text="💎 Сменить тариф",
            callback_data=f"admin_sub_change_tariff:{telegram_id}",
        )

        builder.button(
            text="➕ Продлить доступ",
            callback_data=f"admin_sub_extend:{telegram_id}",
        )

        builder.button(
            text="➖ Уменьшить дни",
            callback_data=f"admin_sub_reduce:{telegram_id}",
        )
    else:
        builder.button(
            text="🎫 Выдать доступ",
            callback_data=f"admin_sub_grant:{telegram_id}",
        )

    builder.button(
        text="← К карточке",
        callback_data=f"admin_user_card:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_change_tariff_keyboard(
    telegram_id: int,
    groups: dict[int, list],
    current_tariff_id: int | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    current_device_limit = None

    if current_tariff_id:
        for device_limit, tariffs in groups.items():
            for t in tariffs:
                if t.id == current_tariff_id:
                    current_device_limit = device_limit
                    break

    for device_limit in sorted(groups.keys()):
        label = get_tariff_group_name(device_limit)

        if device_limit == current_device_limit:
            label += " ✅"

        builder.button(
            text=label,
            callback_data=(
                f"admin_sub_select_group:{telegram_id}:{device_limit}"
            ),
        )

    builder.button(
        text="← Назад",
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_grant_tariff_keyboard(
    telegram_id: int,
    groups: dict[int, list],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for device_limit in sorted(groups.keys()):
        label = get_tariff_group_name(device_limit)

        builder.button(
            text=label,
            callback_data=(
                f"admin_sub_grant_group:{telegram_id}:{device_limit}"
            ),
        )

    builder.button(
        text="← Назад",
        callback_data=f"admin_subscription:{telegram_id}",
    )

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
            callback_data=(
                f"admin_sub_grant_confirm:"
                f"{telegram_id}:{tariff_id}:{days}"
            ),
        )

    builder.button(
        text="∞ Навсегда",
        callback_data=(
            f"admin_sub_grant_confirm:"
            f"{telegram_id}:{tariff_id}:36500"
        ),
    )

    builder.button(
        text="⌨️ Ввести вручную",
        callback_data=(
            f"admin_sub_grant_custom:{telegram_id}:{tariff_id}"
        ),
    )

    builder.button(
        text="← Назад",
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(2, 2, 1, 1)

    return builder.as_markup()


def get_admin_extend_days_new_keyboard(
    telegram_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for days in (7, 30, 90):
        builder.button(
            text=f"{days} дней",
            callback_data=(
                f"admin_sub_confirm_extend:{telegram_id}:{days}"
            ),
        )

    builder.button(
        text="∞ Навсегда",
        callback_data=(
            f"admin_sub_confirm_extend:{telegram_id}:36500"
        ),
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

    builder.button(
        text="✅ Подтвердить",
        callback_data=confirm_callback,
    )

    builder.button(
        text="❌ Отмена",
        callback_data=cancel_callback,
    )

    builder.adjust(2)

    return builder.as_markup()


# ═══════════════════════════════════════════════════════════
# 🔧 УПРАВЛЕНИЕ УСТРОЙСТВАМИ
# ═══════════════════════════════════════════════════════════


def get_admin_user_devices_keyboard(
    telegram_id: int,
    profiles: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for profile in profiles:
        name = (
            getattr(profile, "device_name", None)
            or f"Устройство #{profile.id}"
        )

        builder.button(
            text=f"🗑 {name}",
            callback_data=(
                f"admin_delete_device:{telegram_id}:{profile.id}"
            ),
        )

    builder.button(
        text="← К карточке",
        callback_data=f"admin_user_card:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()