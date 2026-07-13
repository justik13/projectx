"""
Унифицированные названия тарифов для всего проекта.
Устраняет дублирование и рассинхрон между payment.py и profile.py.
"""

def get_tariff_display_name(device_limit: int) -> str:
    """
    Короткое название тарифа для карточек и уведомлений.
    - до 2 устройств: 📱 Базовый
    - до 5 устройств: 👨‍👩‍👧‍👦 Семейный
    - 6+ устройств:   🚀 Pro
    """
    if device_limit <= 2:
        return "📱 Базовый"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный"
    else:
        return "🚀 Pro"


def get_tariff_group_name(device_limit: int) -> str:
    """
    Название группы для клавиатур (с указанием лимита устройств).
    Используется в витрине тарифов и при смене тарифа.
    """
    if device_limit <= 2:
        return "📱 Базовый (2 устр.)"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный (5 устр.)"
    else:
        return f"🚀 Pro ({device_limit} устр.)"