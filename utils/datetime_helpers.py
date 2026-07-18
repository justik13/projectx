"""
Helper функции для работы с временем.
Все временные операции используют aware datetime (с часовым поясом).

Принцип работы:
- В БД храним ВСЕГДА UTC (aware datetime с tzinfo=timezone.utc)
- PostgreSQL TIMESTAMP WITH TIME ZONE автоматически конвертирует в UTC
- При отображении конвертируем в МСК через to_msk()

ЖЁСТКОЕ ПРАВИЛО:
- НИКОГДА не использовать .replace(tzinfo=None)
- НИКОГДА не использовать datetime.now() без timezone
- ВСЕГДА использовать now_utc() или now_msk()
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

# Часовой пояс Москвы для всех отображений
MSK_TZ = ZoneInfo("Europe/Moscow")


def now_utc() -> datetime:
    """
    Текущее время в UTC (aware datetime).
    Используется для записи в БД.
    
    Returns:
        datetime: 2026-07-18 11:30:00+00:00
    """
    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    """
    Текущее время в МСК (aware datetime).
    Используется для локальных проверок (например, daily limit).
    
    Returns:
        datetime: 2026-07-18 14:30:00+03:00
    """
    return datetime.now(MSK_TZ)


def to_msk(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Конвертирует любой aware datetime в МСК.
    
    Args:
        dt: aware datetime (с tzinfo) или None
        
    Returns:
        datetime в МСК или None
        
    Example:
        >>> dt = datetime(2026, 7, 18, 11, 30, tzinfo=timezone.utc)
        >>> to_msk(dt)
        datetime(2026, 7, 18, 14, 30, tzinfo=ZoneInfo("Europe/Moscow"))
    """
    if dt is None:
        return None
    
    # Защита от naive datetime (не должно встречаться в новом коде)
    if dt.tzinfo is None:
        # Предполагаем что naive = UTC (для обратной совместимости)
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt.astimezone(MSK_TZ)


def format_datetime_msk(dt: Optional[datetime], format_str: str = "%d.%m.%Y %H:%M") -> str:
    """
    Форматирует datetime в МСК для отображения.
    
    Args:
        dt: aware datetime или None
        format_str: строка формата strftime
        
    Returns:
        Отформатированная строка в МСК или "—"
        
    Example:
        >>> format_datetime_msk(datetime.now(timezone.utc))
        "18.07.2026 14:30"
    """
    if dt is None:
        return "—"
    
    msk_dt = to_msk(dt)
    return msk_dt.strftime(format_str)


def format_date_msk(dt: Optional[datetime]) -> str:
    """Короткий формат даты (только день и месяц) в МСК."""
    if dt is None:
        return "—"
    
    msk_dt = to_msk(dt)
    return msk_dt.strftime("%d.%m")


def days_left_msk(dt: Optional[datetime]) -> str:
    """
    Форматирует оставшееся время до даты в МСК.
    
    Args:
        dt: aware datetime или None
        
    Returns:
        Строка вида "5 дн. 3 ч." или "—"
    """
    if dt is None:
        return "—"
    
    msk_dt = to_msk(dt)
    now_msk_dt = now_msk()
    
    if msk_dt < now_msk_dt:
        return "—"
    
    diff = msk_dt - now_msk_dt
    days = diff.days
    hours = diff.seconds // 3600
    
    if days > 0:
        return f"{days} дн. {hours} ч."
    else:
        return f"{hours} ч."


def is_expired(dt: Optional[datetime]) -> bool:
    """
    Проверяет, истёк ли datetime относительно текущего UTC времени.
    
    Args:
        dt: aware datetime или None
        
    Returns:
        True если dt < now_utc() или dt is None
    """
    if dt is None:
        return True
    
    # Защита от naive datetime
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    return dt < now_utc()