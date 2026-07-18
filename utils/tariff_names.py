
def get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2:
        return "📱 Базовый"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный"
    else:
        return "🚀 Pro"


def get_tariff_group_name(device_limit: int) -> str:
    if device_limit <= 2:
        return "📱 Базовый (2 устр.)"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный (5 устр.)"
    else:
        return f"🚀 Pro ({device_limit} устр.)"