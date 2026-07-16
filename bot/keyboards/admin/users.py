"""
🔙 НАВИГАЦИЯ — ЖЁСТКОЕ ПРАВИЛО #6
═════════════════════════════════
В КАЖДОЙ InlineKeyboard ОБЯЗАНА быть кнопка "Назад" или "Отмена"

Правило:
- Все функции get_*_keyboard() возвращают клавиатуру с кнопкой "← Назад"
- Кнопка ведёт контекстно (не всегда в главное меню)
- Примеры:
  - Из карточки сервера → к списку серверов
  - Из списка тарифов → в админку
  - Из подписки → к карточке пользователя

Исключения:
- get_admin_confirm_action_keyboard() — имеет "✅ Подтвердить" + "❌ Отмена"
- Эти клавиатуры используются в модалках подтверждения
- Пользователь всегда может нажать "❌ Отмена" для возврата

Проверка перед деплоем:
    grep -n "builder.button" bot/keyboards/admin/users.py | grep -i "назад\|отмена"
    
Каждая функция должна содержать хотя бы одну кнопку возврата.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils.tariff_names import get_tariff_group_name


# ═══════════════════════════════════════════════════════════
# 👤 КАРТОЧКА ПОЛЬЗОВАТЕЛЯ (с динамической кнопкой бана)
# ═══════════════════════════════════════════════════════════

def get_admin_user_card_keyboard(user_id: int, is_banned: bool) -> InlineKeyboardMarkup:
    """
    Клавиатура карточки пользователя в админке.
    
    Кнопки:
    - 📅 Подписка → admin_subscription:{user_id}
    - 🔧 Устройства → admin_user_devices:{user_id}
    - 🚫 Забанить / ✅ Разбанить (динамически)
    - ← К списку пользователей → admin_users (контекстный возврат)
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Подписка", callback_data=f"admin_subscription:{user_id}")
    builder.button(text="🔧 Устройства", callback_data=f"admin_user_devices:{user_id}")
    if is_banned:
        builder.button(text="✅ Разбанить", callback_data=f"admin_unban:{user_id}")
    else:
        builder.button(text="🚫 Забанить", callback_data=f"admin_ban:{user_id}")
    builder.button(text="← К списку пользователей", callback_data="admin_users")
    builder.adjust(1)
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════
# 📅 ПОДПИСКА
# ═══════════════════════════════════════════════════════════

def get_admin_subscription_keyboard(
    telegram_id: int,
    has_active_sub: bool,
) -> InlineKeyboardMarkup:
    """
    Клавиатура управления подпиской пользователя.
    
    Если подписка активна:
    - 💎 Сменить тариф
    - ➕ Продлить доступ
    - ➖ Уменьшить дни
    
    Если подписка неактивна:
    - 🎫 Выдать доступ
    
    Всегда:
    - ← К карточке → admin_user_card:{telegram_id} (контекстный возврат)
    """
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
    groups: dict[int, list],
    current_tariff_id: int | None,
) -> InlineKeyboardMarkup:
    """
    Показывает 3 группы тарифов вместо всех 7.
    
    groups: {device_limit: [tariff1, tariff2, ...]}
    
    Логика:
    - Группируем тарифы по device_limit (2, 5, 10 устр.)
    - Текущий тариф помечаем галочкой ✅
    - Кнопка "← Назад" ведёт к admin_subscription (не в главное меню)
    """
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
            callback_data=f"admin_sub_select_group:{telegram_id}:{device_limit}",
        )
    builder.button(text="← Назад", callback_data=f"admin_subscription:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_grant_tariff_keyboard(
    telegram_id: int,
    groups: dict[int, list],
) -> InlineKeyboardMarkup:
    """
    Показывает 3 группы тарифов для выдачи доступа.
    
    groups: {device_limit: [tariff1, tariff2, ...]}
    
    Отличие от change_tariff:
    - Не помечаем текущий тариф (его может не быть)
    - Кнопка "← Назад" ведёт к admin_subscription
    """
    builder = InlineKeyboardBuilder()
    for device_limit in sorted(groups.keys()):
        label = get_tariff_group_name(device_limit)
        builder.button(
            text=label,
            callback_data=f"admin_sub_grant_group:{telegram_id}:{device_limit}",
        )
    builder.button(text="← Назад", callback_data=f"admin_subscription:{telegram_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_grant_days_keyboard(
    telegram_id: int,
    tariff_id: int,
) -> InlineKeyboardMarkup:
    """
    Выбор срока выдачи доступа.
    
    Пресеты: 7, 30, 90 дней, ∞ Навсегда
    Дополнительно: ⌨️ Ввести вручную
    
    Кнопка "← Назад" ведёт к admin_subscription (не к выбору тарифа,
    чтобы не запутать админа глубокой вложенностью)
    """
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
        callback_data=f"admin_subscription:{telegram_id}",
    )
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def get_admin_extend_days_new_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """
    Выбор срока продления подписки.
    
    Пресеты: 7, 30, 90 дней, ∞ Навсегда
    Дополнительно: ⌨️ Ввести вручную
    
    Кнопка "← Назад" ведёт к admin_subscription
    """
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
    """
    🔥 ИСКЛЮЧЕНИЕ ИЗ ЖЁСТКОГО ПРАВИЛА #6
    
    Модалка подтверждения действия.
    
    Вместо кнопки "← Назад" использует:
    - ✅ Подтвердить — выполняет действие
    - ❌ Отмена — возвращает к предыдущему экрану
    
    Это допустимое исключение, так как:
    - Пользователь уже сделал выбор (тариф, срок, устройство)
    - Нужен явный выбор: применить или отменить
    - Кнопка "❌ Отмена" выполняет роль "Назад"
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=confirm_callback)
    builder.button(text="❌ Отмена", callback_data=cancel_callback)
    builder.adjust(2)
    return builder.as_markup()


# ═══════════════════════════════════════════════════════════
# 🔧 УПРАВЛЕНИЕ УСТРОЙСТВАМИ
# ═══════════════════════════════════════════════════════════

def get_admin_user_devices_keyboard(
    telegram_id: int,
    profiles: list,
) -> InlineKeyboardMarkup:
    """
    Список устройств пользователя для удаления.
    
    Каждая кнопка — одно устройство с эмодзи 🗑
    Нажатие открывает модалку подтверждения удаления.
    
    Кнопка "← К карточке" ведёт к admin_user_card:{telegram_id}
    """
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
    """
    Универсальная кнопка "← Назад" для любого контекста.
    
    Используется в:
    - render_hub() для экранов ввода текста
    - Ошибках валидации
    - Промежуточных экранах
    
    Примеры использования:
    - get_back_button("admin_users") — вернуться к списку пользователей
    - get_back_button(f"admin_subscription:{telegram_id}") — вернуться к подписке
    - get_back_button("back_to_main_menu") — вернуться в главное меню
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="← Назад", callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()