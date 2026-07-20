import logging
import uuid
from typing import Optional

from aiohttp import web
from pydantic import BaseModel, Field, ValidationError

from bot.middlewares.correlation import set_request_id
from database.connection import session_scope
from services.audit_service import AuditService
from services.payment_service import PaymentService
from services.platega_client import PlategaClient

logger = logging.getLogger(__name__)


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
        return self.status.upper()


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