from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# 🔥 Часовой пояс Москвы для всех отображений
MSK_TZ = ZoneInfo("Europe/Moscow")


def to_msk(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Конвертирует naive datetime (который хранится в БД как UTC)
    в aware datetime в часовом поясе Москвы.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # naive datetime в БД — это UTC по соглашению
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK_TZ)


def format_traffic(bytes_value: int) -> str:
    """Форматирует количество байт в читаемый вид (B, KB, MB, GB, TB)"""
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
    """
    Форматирует дату и время в МСК.
    Пример: 15.07.2026 14:30
    """
    if dt is None:
        return "—"
    msk_dt = to_msk(dt)
    return msk_dt.strftime("%d.%m.%Y %H:%M")


def format_days_left(dt: Optional[datetime]) -> str:
    """
    Форматирует оставшееся время до даты в МСК.
    Пример: '5 дн. 3 ч.' или '12 ч.'
    """
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
    """Короткий формат даты (только день и месяц) в МСК"""
    if dt is None:
        return "—"
    msk_dt = to_msk(dt)
    return msk_dt.strftime("%d.%m")