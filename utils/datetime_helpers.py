from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


MSK_TZ = ZoneInfo("Europe/Moscow")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def to_msk(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(MSK_TZ)


def format_datetime_msk(
    dt: Optional[datetime],
    format_str: str = "%d.%m.%Y %H:%M",
) -> str:
    if dt is None:
        return "—"

    msk_dt = to_msk(dt)
    return msk_dt.strftime(format_str)


def days_left_msk(dt: Optional[datetime]) -> str:
    """
    Возвращает человекочитаемый остаток дней.

    Для вечной подписки:
    - если дата 2100 год и дальше;
    - или остаток >= 36500 дней;
    возвращаем "∞", а не "27000 дней".
    """
    if dt is None:
        return "—"

    if dt.year >= 2100:
        return "∞"

    msk_dt = to_msk(dt)
    now_msk_dt = now_msk()

    if msk_dt < now_msk_dt:
        return "—"

    diff = msk_dt - now_msk_dt

    if diff.days >= 36500:
        return "∞"

    days = diff.days
    hours = diff.seconds // 3600

    if days > 0:
        return f"{days} дн. {hours} ч."

    return f"{hours} ч."


def is_expired(dt: Optional[datetime]) -> bool:
    if dt is None:
        return True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt < now_utc()