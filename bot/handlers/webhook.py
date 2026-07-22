import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web
from pydantic import BaseModel, Field, ValidationError

from bot.middlewares.correlation import set_request_id
from database.connection import session_scope
from services.audit_service import AuditService
from services.payment_service import PaymentService
from services.payment_service.alerts import _send_payment_not_found_alert_now
from services.platega_client import PlategaClient

logger = logging.getLogger(__name__)

#
# Максимальный возраст webhook'а в секундах.
# Если createdAt старше этого значения, webhook отклоняется.
#
WEBHOOK_MAX_AGE_SECONDS = 600  # 10 минут


def _is_recent_timestamp(
    created_at: str,
    max_age_seconds: int = WEBHOOK_MAX_AGE_SECONDS,
) -> bool:
    """
    Проверяет, что timestamp не старше max_age_seconds.

    Поддерживает:
    - Unix timestamp в секундах (например "1721750400")
    - Unix timestamp в миллисекундах (например "1721750400000")
    - ISO 8601 (например "2026-07-23T12:00:00Z")

    Если формат не распознан, возвращает True (не отклоняем).
    Это защищает от ложных срабатываний при изменении формата API.
    """
    if not created_at or not isinstance(created_at, str):
        return True

    created_at = created_at.strip()
    if not created_at:
        return True

    # Пробуем Unix timestamp (секунды или миллисекунды)
    try:
        ts = float(created_at)
        if ts > 1e12:
            # Миллисекунды → секунды
            ts = ts / 1000.0
        age = time.time() - ts
        return age <= max_age_seconds
    except (ValueError, TypeError, OverflowError):
        pass

    # Пробуем ISO 8601
    try:
        dt = datetime.fromisoformat(
            created_at.replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (
            datetime.now(timezone.utc) - dt
        ).total_seconds()
        return age <= max_age_seconds
    except (ValueError, TypeError, OverflowError):
        pass

    # Не распознали формат — не отклоняем
    return True


class PlategaCallbackData(BaseModel):
    id: Optional[str] = None
    transactionId: Optional[str] = None
    status: str = Field(
        ...,
        description="CONFIRMED | CANCELED | CHARGEBACKED",
    )
    payload: str = ""
    amount: Optional[float] = None
    currency: Optional[str] = None
    paymentMethod: Optional[int] = None
    createdAt: Optional[str] = None

    model_config = {
        "extra": "allow",
    }

    def get_transaction_id(self) -> Optional[str]:
        return self.transactionId or self.id

    def normalize_status(self) -> str:
        status = str(self.status or "").upper()
        mapping = {
            "SUCCESS": "CONFIRMED",
            "PAID": "CONFIRMED",
            "COMPLETED": "CONFIRMED",
            "CANCELLED": "CANCELED",
            "EXPIRED": "CANCELED",
            "FAILED": "CANCELED",
            "REFUND": "CHARGEBACKED",
            "REFUNDED": "CHARGEBACKED",
            "CHARGEBACK": "CHARGEBACKED",
        }
        return mapping.get(status, status)


async def platega_webhook_handler(
    request: web.Request,
) -> web.Response:
    request_id = uuid.uuid4().hex[:8]
    set_request_id(request_id)

    transaction_id = None
    status = None

    try:
        merchant_id = request.headers.get("X-MerchantId", "")
        secret = request.headers.get("X-Secret", "")

        client = PlategaClient()
        if not client.validate_callback(merchant_id, secret):
            logger.warning(
                "[%s] Invalid payment callback credentials: %s",
                request_id,
                merchant_id,
            )
            return web.Response(
                status=401,
                text="Unauthorized",
            )

        try:
            raw_data = await request.json()
        except Exception as e:
            logger.error(
                "[%s] Failed to parse payment webhook JSON: %s",
                request_id,
                e,
            )
            return web.Response(
                status=400,
                text="Invalid JSON",
            )

        try:
            callback_data = PlategaCallbackData(**raw_data)
        except ValidationError as e:
            logger.error(
                "[%s] Payment webhook validation failed: %s",
                request_id,
                e,
            )
            return web.Response(
                status=400,
                text=f"Validation error: {e.errors()}",
            )

        transaction_id = callback_data.get_transaction_id()
        status = callback_data.normalize_status()
        payload = callback_data.payload

        if not transaction_id:
            logger.warning(
                "[%s] Payment webhook missing transaction ID.",
                request_id,
            )
            return web.Response(
                status=400,
                text=(
                    "Missing transaction ID "
                    "(expected 'id' or 'transactionId')"
                ),
            )

        #
        # ИСПРАВЛЕНО (БАГ 8):
        #
        # Replay-защита по createdAt.
        #
        # Если Platega прислал webhook с createdAt старше
        # WEBHOOK_MAX_AGE_SECONDS (10 минут), отклоняем.
        #
        # Это защищает от replay-атак, когда злоумышленник
        # перехватывает старый webhook и отправляет его повторно.
        #
        # Если createdAt отсутствует или формат не распознан,
        # НЕ отклоняем — это защищает от ложных срабатываний
        # при изменении формата API провайдером.
        #
        if callback_data.createdAt:
            if not _is_recent_timestamp(callback_data.createdAt):
                logger.warning(
                    "[%s] Payment webhook rejected: stale "
                    "createdAt=%s (older than %s seconds). "
                    "transaction=%s",
                    request_id,
                    callback_data.createdAt,
                    WEBHOOK_MAX_AGE_SECONDS,
                    transaction_id,
                )
                return web.Response(
                    status=400,
                    text="Stale webhook",
                )

        valid_statuses = {
            "CONFIRMED",
            "CANCELED",
            "CHARGEBACKED",
        }
        if status not in valid_statuses:
            logger.warning(
                "[%s] Payment webhook unknown status: %s "
                "(transaction=%s)",
                request_id,
                status,
                transaction_id,
            )
            return web.Response(
                status=400,
                text="Invalid webhook status",
            )

        logger.info(
            "[%s] Payment webhook received: transaction=%s, "
            "status=%s, amount=%s, currency=%s",
            request_id,
            transaction_id,
            status,
            callback_data.amount,
            callback_data.currency,
        )

        async with session_scope() as session:
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PLATEGA_CALLBACK",
                    target_type="Payment",
                    target_id=None,
                    details=(
                        f"[{request_id}] "
                        f"transaction={transaction_id}, "
                        f"status={status}, "
                        f"amount={callback_data.amount}"
                    ),
                )
            except Exception as e:
                logger.error(
                    "[%s] Failed to log payment callback "
                    "to audit: %s",
                    request_id,
                    e,
                )

            success, result_code = (
                await PaymentService.handle_platega_callback(
                    session=session,
                    transaction_id=transaction_id,
                    status=status,
                    payload=payload,
                    callback_amount=callback_data.amount,
                    callback_payload=callback_data.payload,
                    callback_currency=callback_data.currency,
                )
            )

            if success:
                if result_code == "not_found":
                    logger.warning(
                        "[%s] Payment callback: payment not found "
                        "for transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    try:
                        await _send_payment_not_found_alert_now(
                            {
                                "transaction_id": transaction_id,
                                "status": status,
                                "source": "platega_webhook",
                            }
                        )
                    except Exception as alert_error:
                        logger.error(
                            "[%s] Failed to send payment not found "
                            "alert: %s",
                            request_id,
                            alert_error,
                        )
                    return web.Response(
                        status=404,
                        text="Payment not found",
                    )

                elif result_code == "already_processed":
                    logger.info(
                        "[%s] Payment callback: already processed "
                        "transaction=%s, status=%s",
                        request_id,
                        transaction_id,
                        status,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "paid_after_cancel":
                    logger.info(
                        "[%s] Payment callback: paid_after_cancel "
                        "transaction=%s. Subscription NOT granted, "
                        "client notified.",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "manual_review":
                    logger.info(
                        "[%s] Payment callback: moved to manual "
                        "review transaction=%s, status=%s",
                        request_id,
                        transaction_id,
                        status,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                else:
                    logger.info(
                        "[%s] Payment callback processed "
                        "successfully: transaction=%s, result=%s",
                        request_id,
                        transaction_id,
                        result_code,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

            else:
                if result_code == "not_found":
                    logger.warning(
                        "[%s] Payment callback: payment not found "
                        "for transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    try:
                        await _send_payment_not_found_alert_now(
                            {
                                "transaction_id": transaction_id,
                                "status": status,
                                "source": "platega_webhook",
                            }
                        )
                    except Exception as alert_error:
                        logger.error(
                            "[%s] Failed to send payment not found "
                            "alert: %s",
                            request_id,
                            alert_error,
                        )
                    return web.Response(
                        status=404,
                        text="Payment not found",
                    )

                elif result_code == "amount_mismatch":
                    logger.error(
                        "[%s] Payment callback: amount mismatch "
                        "transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "payload_mismatch":
                    logger.error(
                        "[%s] Payment callback: payload mismatch "
                        "transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "manual_review":
                    logger.info(
                        "[%s] Payment callback: manual review "
                        "transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "refunded":
                    logger.warning(
                        "[%s] Payment callback: payment already "
                        "refunded transaction=%s",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=200,
                        text="OK",
                    )

                elif result_code == "error":
                    logger.error(
                        "[%s] Payment callback processing failed: "
                        "transaction=%s, status=%s",
                        request_id,
                        transaction_id,
                        status,
                    )
                    return web.Response(
                        status=500,
                        text="Processing failed",
                    )

                else:
                    logger.error(
                        "[%s] Payment callback unknown result_code: "
                        "%s, transaction=%s",
                        request_id,
                        result_code,
                        transaction_id,
                    )
                    return web.Response(
                        status=500,
                        text="Unknown error",
                    )

    except Exception as e:
        logger.error(
            "[%s] Payment webhook error: %s",
            request_id,
            e,
            exc_info=True,
        )
        return web.Response(
            status=500,
            text="Internal server error",
        )


async def healthcheck_handler(
    request: web.Request,
) -> web.Response:
    return web.Response(
        status=200,
        text="OK",
    )


def setup_webhook_routes(app: web.Application):
    app.router.add_post(
        "/webhook/platega",
        platega_webhook_handler,
    )
    app.router.add_get(
        "/health",
        healthcheck_handler,
    )
    logger.info(
        "Payment webhook route registered: POST /webhook/platega"
    )
    logger.info(
        "Healthcheck endpoint registered: GET /health"
    )