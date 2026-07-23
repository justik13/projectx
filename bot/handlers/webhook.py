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
from services.payment_service.alerts import (
    _send_payment_not_found_alert_now,
)
from services.yookassa_client import YooKassaClient

logger = logging.getLogger(__name__)

WEBHOOK_MAX_AGE_SECONDS = 600  # 10 минут


def _is_recent_timestamp(
    created_at: str,
    max_age_seconds: int = WEBHOOK_MAX_AGE_SECONDS,
) -> bool:
    if not created_at or not isinstance(created_at, str):
        return True
    created_at = created_at.strip()
    if not created_at:
        return True
    try:
        ts = float(created_at)
        if ts > 1e12:
            ts = ts / 1000.0
        age = time.time() - ts
        return age <= max_age_seconds
    except (ValueError, TypeError, OverflowError):
        pass
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
    return True


class YooKassaWebhookData(BaseModel):
    """
    Структура webhook-уведомления YooKassa.

    Пример:
    {
      "type": "notification",
      "event": "payment.succeeded",
      "object": {
        "id": "2abc...",
        "status": "succeeded",
        "amount": {"value": "100.00", "currency": "RUB"},
        "description": "Payment #123",
        "metadata": {"payment_id": "123"},
        "paid": true,
        "created_at": "2024-01-01T00:00:00.000+00:00"
      }
    }
    """
    type: str = "notification"
    event: str = Field(..., description="payment.succeeded | payment.canceled | refund.succeeded")
    object: dict = Field(default_factory=dict)

    model_config = {"extra": "allow"}

    def get_payment_id(self) -> Optional[str]:
        return self.object.get("id")

    def get_status(self) -> str:
        return self.object.get("status", "")

    def get_amount_value(self) -> Optional[str]:
        amount = self.object.get("amount")
        if isinstance(amount, dict):
            return amount.get("value")
        return None

    def get_amount_currency(self) -> Optional[str]:
        amount = self.object.get("amount")
        if isinstance(amount, dict):
            return amount.get("currency")
        return None

    def get_metadata(self) -> dict:
        return self.object.get("metadata") or {}

    def get_created_at(self) -> Optional[str]:
        return self.object.get("created_at")

    def normalize_status(self) -> str:
        client = YooKassaClient
        return client.normalize_webhook_event(self.event)


async def yookassa_webhook_handler(
    request: web.Request,
) -> web.Response:
    request_id = uuid.uuid4().hex[:8]
    set_request_id(request_id)
    transaction_id = None
    status = None

    try:
        # ── Аутентификация webhook ──
        shop_id = request.headers.get("X-ShopId", "")
        secret = request.headers.get("X-Secret", "")

        client = YooKassaClient()
        if not client.validate_webhook(shop_id, secret):
            logger.warning(
                "[%s] Invalid YooKassa webhook credentials: %s",
                request_id,
                shop_id,
            )
            return web.Response(status=401, text="Unauthorized")

        # ── Парсинг JSON ──
        try:
            raw_data = await request.json()
        except Exception as e:
            logger.error(
                "[%s] Failed to parse YooKassa webhook JSON: %s",
                request_id,
                e,
            )
            return web.Response(status=400, text="Invalid JSON")

        # ── Валидация структуры ──
        try:
            webhook_data = YooKassaWebhookData(**raw_data)
        except ValidationError as e:
            logger.error(
                "[%s] YooKassa webhook validation failed: %s",
                request_id,
                e,
            )
            return web.Response(
                status=400,
                text=f"Validation error: {e.errors()}",
            )

        transaction_id = webhook_data.get_payment_id()
        status = webhook_data.normalize_status()

        if not transaction_id:
            logger.warning(
                "[%s] YooKassa webhook missing payment ID.",
                request_id,
            )
            return web.Response(
                status=400,
                text="Missing payment ID in webhook object",
            )

        valid_statuses = {"CONFIRMED", "CANCELED", "CHARGEBACKED"}
        if status not in valid_statuses:
            logger.warning(
                "[%s] YooKassa webhook unknown status: %s "
                "(payment=%s)",
                request_id,
                status,
                transaction_id,
            )
            return web.Response(
                status=400,
                text="Invalid webhook status",
            )

        # ── Replay / stale защита ──
        callback_amount_str = webhook_data.get_amount_value()
        callback_currency = webhook_data.get_amount_currency()
        callback_amount = None
        if callback_amount_str is not None:
            try:
                callback_amount = float(callback_amount_str)
            except (ValueError, TypeError):
                pass

        created_at = webhook_data.get_created_at()
        if created_at:
            if not _is_recent_timestamp(created_at):
                logger.info(
                    "[%s] YooKassa webhook is stale: "
                    "created_at=%s. Verifying via API. "
                    "payment=%s",
                    request_id,
                    created_at,
                    transaction_id,
                )
                stale_ok = await _verify_stale_webhook_via_api(
                    webhook_data,
                    status,
                    transaction_id,
                )
                if not stale_ok:
                    logger.warning(
                        "[%s] Stale webhook rejected: "
                        "payment=%s",
                        request_id,
                        transaction_id,
                    )
                    return web.Response(
                        status=400,
                        text="Stale webhook unverified",
                    )
                # Обновляем amount/currency из API
                api_data = await client.get_payment(
                    transaction_id
                )
                if api_data:
                    api_amount = api_data.get("amount", {})
                    if api_amount.get("value"):
                        callback_amount = float(
                            api_amount["value"]
                        )
                    if api_amount.get("currency"):
                        callback_currency = api_amount[
                            "currency"
                        ]

        # ── Извлекаем payload из metadata ──
        metadata = webhook_data.get_metadata()
        payload = metadata.get("payload", "")

        logger.info(
            "[%s] YooKassa webhook received: payment=%s, "
            "status=%s, amount=%s, currency=%s",
            request_id,
            transaction_id,
            status,
            callback_amount,
            callback_currency,
        )

        # ── Аудит ──
        async with session_scope() as session:
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="YOOKASSA_CALLBACK",
                    target_type="Payment",
                    target_id=None,
                    details=(
                        f"[{request_id}] "
                        f"payment={transaction_id}, "
                        f"status={status}, "
                        f"amount={callback_amount}"
                    ),
                )
            except Exception as e:
                logger.error(
                    "[%s] Failed to log YooKassa callback "
                    "to audit: %s",
                    request_id,
                    e,
                )

        # ── Обработка платежа ──
        success, result_code = (
            await PaymentService.handle_yookassa_callback(
                session=session,
                transaction_id=transaction_id,
                status=status,
                payload=payload,
                callback_amount=callback_amount,
                callback_payload=payload,
                callback_currency=callback_currency,
            )
        )

        if success:
            if result_code == "not_found":
                logger.warning(
                    "[%s] YooKassa callback: payment not "
                    "found for payment=%s",
                    request_id,
                    transaction_id,
                )
                try:
                    await _send_payment_not_found_alert_now(
                        {
                            "transaction_id": transaction_id,
                            "status": status,
                            "source": "yookassa_webhook",
                        }
                    )
                except Exception as alert_error:
                    logger.error(
                        "[%s] Failed to send alert: %s",
                        request_id,
                        alert_error,
                    )
                return web.Response(
                    status=404,
                    text="Payment not found",
                )
            elif result_code == "already_processed":
                return web.Response(status=200, text="OK")
            elif result_code == "paid_after_cancel":
                return web.Response(status=200, text="OK")
            elif result_code == "manual_review":
                return web.Response(status=200, text="OK")
            else:
                return web.Response(status=200, text="OK")
        else:
            if result_code == "not_found":
                try:
                    await _send_payment_not_found_alert_now(
                        {
                            "transaction_id": transaction_id,
                            "status": status,
                            "source": "yookassa_webhook",
                        }
                    )
                except Exception as alert_error:
                    logger.error(
                        "[%s] Failed to send alert: %s",
                        request_id,
                        alert_error,
                    )
                return web.Response(
                    status=404,
                    text="Payment not found",
                )
            elif result_code in (
                "amount_mismatch",
                "payload_mismatch",
                "manual_review",
                "refunded",
            ):
                return web.Response(status=200, text="OK")
            elif result_code == "error":
                return web.Response(
                    status=500,
                    text="Processing failed",
                )
            else:
                return web.Response(
                    status=500,
                    text="Unknown error",
                )

    except Exception as e:
        logger.error(
            "[%s] YooKassa webhook error: %s",
            request_id,
            e,
            exc_info=True,
        )
        return web.Response(
            status=500,
            text="Internal server error",
        )


async def _verify_stale_webhook_via_api(
    webhook_data: YooKassaWebhookData,
    normalized_status: str,
    transaction_id: str,
) -> bool:
    """
    Дополнительная проверка старого webhook через YooKassa API.
    """
    client = YooKassaClient()
    api_data = await client.get_payment(transaction_id)
    if not api_data:
        return False

    api_status_raw = api_data.get("status", "")
    api_status_map = {
        "succeeded": "CONFIRMED",
        "canceled": "CANCELED",
        "pending": "PENDING",
        "processing": "PROCESSING",
    }
    api_status = api_status_map.get(
        api_status_raw, api_status_raw.upper()
    )

    if api_status != normalized_status:
        logger.warning(
            "Stale webhook status mismatch: "
            "callback=%s, api=%s, payment=%s",
            normalized_status,
            api_status,
            transaction_id,
        )
        return False

    # Проверка суммы
    callback_amount_str = webhook_data.get_amount_value()
    api_amount = api_data.get("amount", {})
    api_amount_str = api_amount.get("value")

    if callback_amount_str and api_amount_str:
        try:
            if float(callback_amount_str) != float(api_amount_str):
                logger.warning(
                    "Stale webhook amount mismatch: "
                    "callback=%s, api=%s, payment=%s",
                    callback_amount_str,
                    api_amount_str,
                    transaction_id,
                )
                return False
        except (ValueError, TypeError):
            pass

    return True


async def healthcheck_handler(
    request: web.Request,
) -> web.Response:
    return web.Response(status=200, text="OK")


def setup_webhook_routes(app: web.Application):
    app.router.add_post(
        "/webhook/yookassa",
        yookassa_webhook_handler,
    )
    app.router.add_get(
        "/health",
        healthcheck_handler,
    )
    logger.info(
        "YooKassa webhook route registered: "
        "POST /webhook/yookassa"
    )
    logger.info(
        "Healthcheck endpoint registered: GET /health"
    )