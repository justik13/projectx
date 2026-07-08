from datetime import datetime
from typing import Optional

def format_traffic(bytes_value: int) -> str:
    """Форматирует количество байт в читаемый вид"""
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
    """Форматирует дату и время"""
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M")

def format_days_left(dt: Optional[datetime]) -> str:
    """Форматирует оставшиеся дни до даты"""
    if dt is None:
        return "—"
    
    now = datetime.utcnow()
    if dt < now:
        return "—"
    
    diff = dt - now
    days = diff.days
    hours = diff.seconds // 3600
    
    if days > 0:
        return f"{days} дн. {hours} ч."
    else:
        return f"{hours} ч."
