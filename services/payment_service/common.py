import logging
from decimal import Decimal, InvalidOperation

import redis.asyncio as aioredis
from cachetools import TTLCache

from config.settings import get_settings

logger = logging.getLogger(__name__)

_alerted_paid_after_cancel: TTLCache = TTLCache(
    maxsize=100000,
    ttl=86400,
)
_notified_paid_after_cancel: TTLCache = TTLCache(
    maxsize=100000,
    ttl=86400,
)
_alerted_manual_review: TTLCache = TTLCache(
    maxsize=100000,
    ttl=86400,
)
_alerted_payment_not_found: TTLCache = TTLCache(
    maxsize=100000,
    ttl=3600,
)

_redis_client: aioredis.Redis | None = None

MANUAL_REVIEW_REASONS = {
    "banned_or_deleted": "Пользователь заблокирован или удалён",
    "inactive_tariff": "Тариф неактивен",
    "amount_mismatch": "Сумма платежа не совпадает",
    "amount_missing": "Не удалось получить сумму платежа",
    "currency_mismatch": "Валюта платежа не совпадает",
    "payload_mismatch": "Несовпадение идентификатора платежа",
    "missing_tariff_or_user": "Не найден тариф или пользователь",
    "missing_snapshot": "Не найдены условия покупки",
    "device_limit_exceeded": "Превышен лимит устройств",
    "status_failed": "Платёж находился в статусе failed",
    "payment_create_error": "Ошибка создания платежа",
    "cancel_after_completed": "Отмена после успешной оплаты",
    "not_found": "Платёж не найден",
    "owner_mismatch": "Платёж не принадлежит пользователю",
}

MANUAL_GRANT_ALLOWED_STATUSES = {
    "pending",
    "cancelled",
    "failed",
    "requires_manual_review",
}


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.close()
        finally:
            _redis_client = None


def _to_decimal(value) -> Decimal | None:
    """
    Безопасно конвертирует значение в Decimal.
    Использовать для финансовых данных.
    Никогда не использовать float-сравнения для денег.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _get_payment_snapshot_duration(payment) -> int | None:
    """
    Возвращает длительность покупки.
    Приоритет:
    1. snapshot_duration_days из платежа;
    2. текущий тариф, если snapshot отсутствует.
    """
    snapshot_value = getattr(
        payment,
        "snapshot_duration_days",
        None,
    )
    if snapshot_value is not None:
        try:
            return int(snapshot_value)
        except (TypeError, ValueError):
            pass
    tariff = getattr(payment, "tariff", None)
    if tariff:
        return getattr(tariff, "duration_days", None)
    return None


def _get_payment_snapshot_device_limit(payment) -> int | None:
    """
    Возвращает лимит устройств покупки.
    Приоритет:
    1. snapshot_device_limit из платежа;
    2. текущий тариф, если snapshot отсутствует.
    """
    snapshot_value = getattr(
        payment,
        "snapshot_device_limit",
        None,
    )
    if snapshot_value is not None:
        try:
            return int(snapshot_value)
        except (TypeError, ValueError):
            pass
    tariff = getattr(payment, "tariff", None)
    if tariff:
        return getattr(tariff, "device_limit", None)
    return None


def _build_payment_snapshot(payment) -> dict:
    """
    Создаёт безопасный snapshot платежа для отправки алертов
    после commit.

    Важно:
    - snapshot не содержит SQLAlchemy-объекты;
    - его можно использовать в post-commit задачах;
    - личные данные минимизированы.
    """
    user = getattr(payment, "user", None)
    duration_days = _get_payment_snapshot_duration(payment)
    device_limit = _get_payment_snapshot_device_limit(payment)

    tariff_name = "—"
    if duration_days is not None and device_limit is not None:
        tariff_name = f"{duration_days} дн. / {device_limit} устр."

    return {
        "payment_id": payment.id,
        "user_telegram_id": (
            user.telegram_id
            if user
            else None
        ),
        "username": (
            f"@{user.username}"
            if user and user.username
            else "—"
        ),
        "amount": str(payment.amount),
        "currency": payment.currency,
        "tariff_name": tariff_name,
        "payment_method": (
            payment.payment_method
            if getattr(payment, "payment_method", None)
            else "—"
        ),
        "external_id": (
            payment.external_id
            if getattr(payment, "external_id", None)
            else "—"
        ),
    }