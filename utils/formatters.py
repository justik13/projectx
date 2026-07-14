from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, List

# Часовой пояс Москвы для всех отображений
MSK_TZ = ZoneInfo("Europe/Moscow")


def to_msk(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Конвертирует naive datetime (который хранится в БД как UTC)
    в aware datetime в часовом поясе Москвы.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK_TZ)


def format_traffic(bytes_value: int) -> str:
    """Форматирует количество байт в читаемый вид (B, KB, MB, GB, TB)."""
    if bytes_value == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = bytes_value
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def format_datetime(dt: Optional[datetime]) -> str:
    """Форматирует дату и время в МСК. Пример: 15.07.2026 14:30."""
    if dt is None:
        return "—"
    msk_dt = to_msk(dt)
    return msk_dt.strftime("%d.%m.%Y %H:%M")


def format_days_left(dt: Optional[datetime]) -> str:
    """Форматирует оставшееся время до даты в МСК."""
    if dt is None:
        return "—"
    msk_dt = to_msk(dt)
    now_msk = datetime.now(MSK_TZ)
    if msk_dt < now_msk:
        return "—"
    diff = msk_dt - now_msk
    days = diff.days
    hours = diff.seconds // 3600
    if days > 0:
        return f"{days} дн. {hours} ч."
    else:
        return f"{hours} ч."


def format_datetime_short(dt: Optional[datetime]) -> str:
    """Короткий формат даты (только день и месяц) в МСК."""
    if dt is None:
        return "—"
    msk_dt = to_msk(dt)
    return msk_dt.strftime("%d.%m")


# ============================================================
# FORMATTER SERVICES (вынесено из handlers)
# ============================================================

def format_user_card_text(
    user,
    profiles: list,
    referrals: list,
    now: datetime,
) -> str:
    """
    Форматирует текст карточки пользователя для админки.
    Вынесено из bot/handlers/admin/users.py для переиспользования.
    """
    from bot import texts
    from utils.telegram import safe

    has_access = user.subscription_end and user.subscription_end > now

    return texts.ADMIN_USER_CARD.format(
        telegram_id=user.telegram_id,
        username=safe(user.username),
        first_name=safe(user.first_name),
        status=("🟢 Активен" if has_access else "🔴 Неактивен"),
        ban=("🚫 ЗАБАНЕН" if user.is_banned else "✅ Не забанен"),
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        devices_count=len(profiles),
        device_limit=user.device_limit,
        referrals_count=len(referrals),
        referral_days=user.referral_days,
        created_at=format_datetime(user.created_at),
    )


def format_connection_device_card(
    profile,
    server_flag: str,
    server_name: str,
    last_connected_text: str,
) -> str:
    """
    Форматирует карточку устройства для экрана подключений.
    Вынесено из bot/handlers/connection.py.
    """
    from bot import texts
    from utils.telegram import safe

    traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)

    return texts.DEVICE_CARD.format(
        device_name=safe(profile.device_name),
        flag=server_flag,
        server_name=safe(server_name),
        last_connected_text=last_connected_text,
        traffic_down=format_traffic(profile.traffic_down),
        traffic_up=format_traffic(profile.traffic_up),
        traffic_total=traffic_total,
    )