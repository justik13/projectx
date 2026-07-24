import logging
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.middlewares.user_context import invalidate_user_cache
from database.connection import queue_post_commit_task
from database.models import Payment
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_for_update,
    get_user_payments,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import (
    get_user_by_telegram_id,
)
from services.audit_service import AuditService
from services.yookassa_client import YooKassaClient
from services.profile_deletion_service import (
    ProfileDeletionService,
)
from services.referral_service import ReferralService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc

from .alerts import (
    _notify_client_chargeback_now,
    _notify_client_paid_after_cancel_now,
    _send_cancel_after_completed_alert_now,
    _send_chargeback_alert_now,
    _send_manual_review_alert_now,
    _send_paid_after_cancel_alert_now,
)
from .common import (
    MANUAL_GRANT_ALLOWED_STATUSES,
    _build_payment_snapshot,
    _get_payment_snapshot_device_limit,
    _get_payment_snapshot_duration,
    _get_redis,
    _to_decimal,
)

try:
    from database.repositories.payments_repo import (
        log_payment_event,
    )
except Exception:
    log_payment_event = None

logger = logging.getLogger(__name__)


async def _log_event_safe(
    session: AsyncSession,
    payment_id: int,
    event_type: str,
    *,
    provider_status: str | None = None,
    reason: str | None = None,
    source: str | None = None,
    details: str | None = None,
) -> None:
    if log_payment_event is None:
        return
    try:
        async with session.begin_nested():
            await log_payment_event(
                session,
                payment_id,
                event_type,
                provider_status=provider_status,
                reason=reason,
                source=source,
                details=details,
            )
    except Exception as e:
        logger.warning(
            "Failed to log payment event %s for payment %s: %s",
            event_type,
            payment_id,
            e,
        )


class PaymentService:
    @staticmethod
    async def _apply_payment_snapshot(
        session: AsyncSession,
        payment: Payment,
        tariff,
    ) -> None:
        if not tariff:
            return
        snapshot_fields = {
            "snapshot_duration_days": getattr(
                tariff, "duration_days", None,
            ),
            "snapshot_device_limit": getattr(
                tariff, "device_limit", None,
            ),
            "snapshot_amount": payment.amount,
            "snapshot_currency": payment.currency,
        }
        changed = False
        for field_name, field_value in snapshot_fields.items():
            if hasattr(payment, field_name):
                setattr(payment, field_name, field_value)
                changed = True
        if changed:
            await session.flush()

    @staticmethod
    async def _mark_manual_review_direct(
        session: AsyncSession,
        payment: Payment,
        reason: str,
        source: str,
    ) -> None:
        payment.status = "requires_manual_review"
        payment.manual_review_reason = reason
        await session.flush()

        await _log_event_safe(
            session, payment.id, "manual_review",
            reason=reason, source=source,
        )

        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAYMENT_MANUAL_REVIEW",
            target_type="Payment",
            target_id=payment.id,
            details=f"reason={reason}, source={source}",
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot, r=reason, src=source: (
                _send_manual_review_alert_now(s, r, src)
            ),
        )

    # ─────────────────────────────────────────────────────────────
    # Обработка успешного платежа
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        payment_obj = await session.get(Payment, payment_id)
        if not payment_obj:
            return False, "not_found"

        redis_lock = None
        acquired = False

        try:
            redis = await _get_redis()
            user_lock_key = (
                f"lock:payment_bonus:{payment_obj.user_id}"
            )
            redis_lock = redis.lock(
                user_lock_key, timeout=30, blocking_timeout=15,
            )
            acquired = await redis_lock.acquire()
        except Exception as e:
            logger.warning(
                "Payment %s: Redis unavailable: %s",
                payment_id, e,
            )
            redis_lock = None
            acquired = False

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session, payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "completed":
                    return True, "already_processed"

                if payment.status == "cancelled":
                    snapshot = _build_payment_snapshot(payment)
                    await _log_event_safe(
                        session, payment.id,
                        "paid_after_cancel",
                        source="handle_successful_payment",
                    )
                    await AuditService.log_action(
                        session, admin_id=0,
                        action="PAID_AFTER_CANCEL",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"user="
                            f"{snapshot.get('user_telegram_id')}, "
                            f"amount={snapshot.get('amount')} "
                            f"{snapshot.get('currency')}"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot: (
                            _send_paid_after_cancel_alert_now(s)
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot: (
                            _notify_client_paid_after_cancel_now(s)
                        ),
                    )
                    return True, "paid_after_cancel"

                if payment.status == "refunded":
                    return False, "refunded"

                if payment.status == "requires_manual_review":
                    return False, "manual_review"

                if payment.status == "failed":
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        "status_failed",
                        "handle_successful_payment",
                    )
                    return False, "manual_review"

                user = payment.user
                tariff = payment.tariff

                manual_review_reason = None
                duration_days = _get_payment_snapshot_duration(
                    payment
                )
                device_limit = _get_payment_snapshot_device_limit(
                    payment
                )

                if not user:
                    manual_review_reason = "missing_tariff_or_user"
                elif user.is_deleted or user.is_banned:
                    manual_review_reason = "banned_or_deleted"
                elif (
                    payment.amount is None
                    or payment.amount <= 0
                ):
                    manual_review_reason = "amount_missing"
                elif (
                    duration_days is None
                    or device_limit is None
                ):
                    manual_review_reason = "missing_snapshot"
                elif tariff and not tariff.is_active:
                    manual_review_reason = "inactive_tariff"

                if manual_review_reason:
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        manual_review_reason,
                        "handle_successful_payment",
                    )
                    return False, "manual_review"

                payment.status = "completed"
                payment.paid_at = now_utc()
                await session.flush()

                await _log_event_safe(
                    session, payment.id, "completed",
                    source="handle_successful_payment",
                )

                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        duration_days,
                        new_device_limit=device_limit,
                        new_tariff_id=(
                            tariff.id if tariff else None
                        ),
                    )
                except ValueError:
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        "device_limit_exceeded",
                        "handle_successful_payment_extend",
                    )
                    return False, "manual_review"
                except Exception as e:
                    logger.error(
                        "Payment %s: extend error: %s",
                        payment_id, e, exc_info=True,
                    )
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        "status_failed",
                        "handle_successful_payment_extend",
                    )
                    return False, "manual_review"

                # Реферальные бонусы
                payments = await get_user_payments(
                    session, user.id,
                )
                successful_payments = [
                    p for p in payments
                    if p.status == "completed"
                ]
                is_first_payment = (
                    len(successful_payments) == 1
                )

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=duration_days,
                        )
                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed for "
                            "payment %s: %s",
                            payment_id, e,
                        )

                user.last_payment_at = now_utc()
                await session.flush()
                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session, admin_id=0,
                        action="PAYMENT_SUCCESS",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"user={user.telegram_id}, "
                            f"amount={payment.amount} "
                            f"{payment.currency}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to log payment success: %s", e,
                    )

                return True, "success"

        except Exception as e:
            logger.error(
                "Failed to process payment %s: %s",
                payment_id, e, exc_info=True,
            )
            return False, "error"

        finally:
            if redis_lock is not None and acquired:
                try:
                    await redis_lock.release()
                except Exception:
                    pass

    # ─────────────────────────────────────────────────────────────
    # Ручная выдача
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def force_grant_payment(
        session: AsyncSession,
        payment_id: int,
        admin_id: int,
    ) -> tuple:
        allowed_statuses = MANUAL_GRANT_ALLOWED_STATUSES

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session, payment_id,
                )
                if not payment:
                    return False, "Платёж не найден"

                if payment.status == "completed":
                    return False, "Платёж уже выдан"

                if payment.status == "refunded":
                    return False, "Платёж возвращён"

                if payment.status not in allowed_statuses:
                    return False, "Недопустимый статус"

                user = payment.user
                if not user:
                    return False, "Пользователь не найден"
                if user.is_deleted:
                    return False, "Пользователь удалён"
                if user.is_banned:
                    return False, "Пользователь заблокирован"

                duration_days = _get_payment_snapshot_duration(
                    payment
                )
                device_limit = _get_payment_snapshot_device_limit(
                    payment
                )

                if duration_days is None or device_limit is None:
                    return False, "Не найдены условия покупки"

                payment.status = "completed"
                if not payment.paid_at:
                    payment.paid_at = now_utc()
                await session.flush()

                await _log_event_safe(
                    session, payment.id, "manual_grant",
                    source="force_grant_payment",
                    details=f"admin_id={admin_id}",
                )

                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        duration_days,
                        new_device_limit=device_limit,
                        new_tariff_id=(
                            payment.tariff.id
                            if payment.tariff else None
                        ),
                    )
                except ValueError:
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        "device_limit_exceeded",
                        "force_grant_payment",
                    )
                    return False, "Превышен лимит устройств"
                except Exception as e:
                    await PaymentService._mark_manual_review_direct(
                        session, payment,
                        "status_failed",
                        "force_grant_payment",
                    )
                    return False, f"Ошибка продления: {e}"

                payments = await get_user_payments(
                    session, user.id,
                )
                successful_payments = [
                    p for p in payments
                    if p.status == "completed"
                ]
                is_first_payment = (
                    len(successful_payments) == 1
                )

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=duration_days,
                        )
                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed: %s", e,
                        )

                user.last_payment_at = now_utc()
                await session.flush()
                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session, admin_id=admin_id,
                        action="MANUAL_GRANT",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"Admin {admin_id} granted "
                            f"payment {payment_id}"
                        ),
                    )
                except Exception:
                    pass

                return True, "ok"

        except Exception as e:
            logger.error(
                "force_grant_payment failed: %s",
                e, exc_info=True,
            )
            return False, f"Ошибка БД: {e}"

    # ─────────────────────────────────────────────────────────────
    # Создание платежа через YooKassa
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def create_yookassa_payment(
        session: AsyncSession,
        user_id: int,
        tariff_id: int,
        amount: float,
        telegram_id: int,
        bot_username: str,
    ) -> tuple:
        from config.settings import get_settings

        settings = get_settings()
        decimal_amount = _to_decimal(amount)
        if decimal_amount is None:
            logger.error(
                "create_yookassa_payment: invalid amount %s",
                amount,
            )
            return None, None

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            return None, None

        payment = await create_payment(
            session=session,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=decimal_amount,
            currency="RUB",
        )
        await PaymentService._apply_payment_snapshot(
            session, payment, tariff,
        )

        await _log_event_safe(
            session, payment.id, "payment_created",
            source="yookassa",
        )

        description = f"Payment #{payment.id}"
        clean_username = bot_username.lstrip("@")
        return_url = settings.YOOKASSA_RETURN_URL.format(
            bot_username=clean_username,
        )
        payload = f"payment_{payment.id}"

        client = YooKassaClient()
        yk_payment = await client.create_payment(
            amount=decimal_amount,
            currency="RUB",
            description=description,
            return_url=return_url,
            metadata={"payload": payload},
        )

        if not yk_payment:
            payment.status = "failed"
            payment.manual_review_reason = "payment_create_error"
            try:
                await session.flush()
            except Exception:
                pass
            await _log_event_safe(
                session, payment.id, "payment_failed",
                reason="provider_create_failed",
                source="yookassa",
            )
            try:
                await AuditService.log_action(
                    session, admin_id=0,
                    action="PAYMENT_FAILED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"user={user_id}, "
                        f"amount={decimal_amount} RUB, "
                        f"YooKassa create failed"
                    ),
                )
            except Exception:
                pass
            return None, None

        external_id = yk_payment.get("id")
        confirmation = yk_payment.get("confirmation", {})
        payment_url = confirmation.get("confirmation_url")

        if not external_id or not payment_url:
            payment.status = "failed"
            payment.manual_review_reason = "payment_create_error"
            try:
                await session.flush()
            except Exception:
                pass
            await _log_event_safe(
                session, payment.id, "payment_failed",
                reason="missing_id_or_url",
                source="yookassa",
            )
            return None, None

        payment.external_id = str(external_id)
        payment.payment_url = str(payment_url)
        payment.payment_method = "YooKassa"
        await session.flush()

        return payment, None

    # ─────────────────────────────────────────────────────────────
    # Обработка webhook от YooKassa
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def handle_yookassa_callback(
        session: AsyncSession,
        transaction_id: str,
        status: str,
        payload: str,
        callback_amount: float | None = None,
        callback_payload: str | None = None,
        callback_currency: str | None = None,
    ) -> tuple:
        #
        # ИСПРАВЛЕНО (Фаза 3, фикс 9):
        #
        # Добавлен statement_timeout для защиты от deadlock.
        # SELECT FOR UPDATE блокирует строку до конца транзакции.
        # Если транзакция долгая (extend_subscription, referral,
        # audit), параллельные webhook'и висят в очереди.
        #
        # SET LOCAL действует только до конца текущей транзакции.
        #
        try:
            await session.execute(
                text("SET LOCAL statement_timeout = '10s'")
            )
        except Exception as e:
            logger.warning(
                "Failed to set statement_timeout: %s", e,
            )

        stmt = (
            select(Payment)
            .options(
                selectinload(Payment.user),
                selectinload(Payment.tariff),
            )
            .where(Payment.external_id == transaction_id)
            .with_for_update()
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            return False, "not_found"

        await _log_event_safe(
            session, payment.id, "provider_callback",
            provider_status=status,
            source="yookassa_callback",
            details=f"transaction_id={transaction_id}",
        )

        if status == "CONFIRMED":
            if payment.status == "completed":
                return True, "already_processed"

            if payment.status == "cancelled":
                snapshot = _build_payment_snapshot(payment)
                await _log_event_safe(
                    session, payment.id,
                    "paid_after_cancel",
                    provider_status=status,
                    source="yookassa_callback",
                )
                await AuditService.log_action(
                    session, admin_id=0,
                    action="PAID_AFTER_CANCEL",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _send_paid_after_cancel_alert_now(s)
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _notify_client_paid_after_cancel_now(s)
                    ),
                )
                return True, "paid_after_cancel"

            if payment.status == "refunded":
                return False, "refunded"

            # Верификация суммы
            if callback_amount is None:
                client = YooKassaClient()
                api_data = await client.get_payment(
                    transaction_id,
                )
                if api_data:
                    api_amount = api_data.get("amount", {})
                    val = api_amount.get("value")
                    if val:
                        callback_amount = float(val)

            if callback_amount is None:
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "amount_missing",
                    source="yookassa_callback",
                )
                return False, "manual_review"

            callback_decimal = _to_decimal(callback_amount)
            if callback_decimal is None:
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "amount_mismatch",
                    source="yookassa_callback",
                )
                return False, "manual_review"

            if payment.amount != callback_decimal:
                logger.error(
                    "YooKassa amount mismatch: DB=%s, "
                    "callback=%s, payment_id=%s",
                    payment.amount, callback_decimal,
                    payment.id,
                )
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "amount_mismatch",
                    source="yookassa_callback",
                )
                return False, "manual_review"

            # Верификация валюты
            if callback_currency:
                cb_cur = str(callback_currency).upper()
                db_cur = str(payment.currency).upper()
                if db_cur != cb_cur:
                    await PaymentService._set_manual_review(
                        session, payment.id,
                        "currency_mismatch",
                        source="yookassa_callback",
                    )
                    return False, "manual_review"

            # Верификация payload
            expected_payload = f"payment_{payment.id}"
            if (
                callback_payload not in (None, "")
                and callback_payload != expected_payload
            ):
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "payload_mismatch",
                    source="yookassa_callback",
                )
                return False, "manual_review"

            success, result_code = (
                await PaymentService.handle_successful_payment(
                    session, payment.id,
                )
            )
            return success, result_code

        elif status == "CANCELED":
            if payment.status == "refunded":
                return True, "already_processed"

            if payment.status == "cancelled":
                return True, "already_processed"

            if payment.status == "completed":
                snapshot = _build_payment_snapshot(payment)
                await _log_event_safe(
                    session, payment.id,
                    "cancel_after_completed",
                    provider_status=status,
                    source="yookassa_callback",
                )
                await AuditService.log_action(
                    session, admin_id=0,
                    action="PAYMENT_CANCEL_AFTER_COMPLETED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_cancel_after_completed_alert_now(
                            s, tid,
                        )
                    ),
                )
                return True, "manual_review"

            payment.status = "cancelled"
            await session.flush()

            await _log_event_safe(
                session, payment.id, "cancelled",
                provider_status=status,
                source="yookassa_callback",
            )

            try:
                await AuditService.log_action(
                    session, admin_id=0,
                    action="PAYMENT_CANCELLED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"YooKassa callback: "
                        f"transaction={transaction_id}"
                    ),
                )
            except Exception:
                pass

            return True, "success"

        elif status == "CHARGEBACKED":
            return await PaymentService._process_chargeback(
                session, payment.id, transaction_id,
            )

        return False, "error"

    # ─────────────────────────────────────────────────────────────
    # Проверка статуса платежа (кнопка «Я оплатил»)
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def check_yookassa_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        payment = await get_payment_by_id(
            session, payment_id,
        )
        if not payment or not payment.external_id:
            return False, "not_found"

        if payment.status == "completed":
            return True, "success"

        client = YooKassaClient()

        if payment.status == "cancelled":
            api_data = await client.get_payment(
                payment.external_id,
            )
            if api_data:
                provider_status = api_data.get("status")
                if provider_status == "succeeded":
                    amount_obj = api_data.get("amount", {})
                    cb_amount = amount_obj.get("value")
                    if cb_amount is None:
                        await PaymentService._set_manual_review(
                            session, payment.id,
                            "amount_missing",
                            source="check_yookassa_cancelled",
                        )
                        return False, "manual_review"

                    cb_decimal = _to_decimal(cb_amount)
                    if (
                        cb_decimal is None
                        or payment.amount != cb_decimal
                    ):
                        await PaymentService._set_manual_review(
                            session, payment.id,
                            "amount_mismatch",
                            source="check_yookassa_cancelled",
                        )
                        return False, "manual_review"

                    cb_currency = amount_obj.get("currency")
                    if cb_currency:
                        if (
                            str(payment.currency).upper()
                            != str(cb_currency).upper()
                        ):
                            await PaymentService._set_manual_review(
                                session, payment.id,
                                "currency_mismatch",
                                source="check_yookassa_cancelled",
                            )
                            return False, "manual_review"

                    return (
                        await PaymentService
                        .handle_successful_payment(
                            session, payment.id,
                        )
                    )

                if provider_status == "canceled":
                    return False, "cancelled"

            return False, "cancelled"

        if payment.status == "requires_manual_review":
            return False, "manual_review"

        if payment.status == "refunded":
            return False, "refunded"

        if payment.status != "pending":
            return False, "invalid_status"

        api_data = await client.get_payment(
            payment.external_id,
        )
        if not api_data:
            return False, "api_error"

        provider_status = api_data.get("status")

        if provider_status == "succeeded":
            amount_obj = api_data.get("amount", {})
            cb_amount = amount_obj.get("value")
            if cb_amount is None:
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "amount_missing",
                    source="check_yookassa_payment",
                )
                return False, "manual_review"

            cb_decimal = _to_decimal(cb_amount)
            if cb_decimal is None or payment.amount != cb_decimal:
                await PaymentService._set_manual_review(
                    session, payment.id,
                    "amount_mismatch",
                    source="check_yookassa_payment",
                )
                return False, "manual_review"

            cb_currency = amount_obj.get("currency")
            if cb_currency:
                if (
                    str(payment.currency).upper()
                    != str(cb_currency).upper()
                ):
                    await PaymentService._set_manual_review(
                        session, payment.id,
                        "currency_mismatch",
                        source="check_yookassa_payment",
                    )
                    return False, "manual_review"

            return (
                await PaymentService.handle_successful_payment(
                    session, payment.id,
                )
            )

        elif provider_status == "canceled":
            if payment.status == "completed":
                snapshot = _build_payment_snapshot(payment)
                tid = payment.external_id or "—"
                await _log_event_safe(
                    session, payment.id,
                    "cancel_after_completed",
                    provider_status=provider_status,
                    source="check_yookassa_payment",
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot, t=tid: (
                        _send_cancel_after_completed_alert_now(
                            s, t,
                        )
                    ),
                )
                return False, "manual_review"

            if payment.status != "cancelled":
                payment.status = "cancelled"
                await session.flush()
            return False, "cancelled"

        return False, "pending"

    # ─────────────────────────────────────────────────────────────
    # Manual review
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _set_manual_review(
        session: AsyncSession,
        payment_id: int,
        reason: str,
        source: str,
    ) -> tuple:
        stmt = (
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status.in_(
                    ["pending", "failed", "cancelled"],
                ),
            )
            .values(
                status="requires_manual_review",
                manual_review_reason=reason,
            )
        )
        result = await session.execute(stmt)
        await session.flush()

        if result.rowcount == 0:
            current = await session.get(Payment, payment_id)
            if current and current.status == "completed":
                return True, "already_processed"
            if (
                current
                and current.status == "requires_manual_review"
            ):
                return True, "manual_review"
            return (
                False,
                current.status if current else "not_found",
            )

        payment = await get_payment_by_id(
            session, payment_id,
        )

        await _log_event_safe(
            session, payment_id, "manual_review",
            reason=reason, source=source,
        )

        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session, admin_id=0,
            action="PAYMENT_MANUAL_REVIEW",
            target_type="Payment",
            target_id=payment_id,
            details=(
                f"reason={reason}, source={source}, "
                f"user={payment.user_id if payment else '—'}"
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot, r=reason, src=source: (
                _send_manual_review_alert_now(s, r, src)
            ),
        )

        return True, "manual_review"

    # ─────────────────────────────────────────────────────────────
    # Chargeback
    # ─────────────────────────────────────────────────────────────

    @staticmethod
    async def _process_chargeback(
        session: AsyncSession,
        payment_id: int,
        transaction_id: str,
    ) -> tuple:
        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session, payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "refunded":
                    return True, "already_processed"

                was_completed = (
                    payment.status == "completed"
                )

                payment.status = "refunded"
                payment.manual_review_reason = None
                await session.flush()

                await _log_event_safe(
                    session, payment.id, "chargeback",
                    provider_status="CHARGEBACKED",
                    source="payment_service",
                    details=(
                        f"transaction_id={transaction_id}"
                    ),
                )

                user = payment.user
                if user:
                    current_time = now_utc()

                    if was_completed:
                        user.subscription_end = current_time
                        user.current_tariff_id = None
                        user.device_limit = 0
                        await session.flush()

                        duration_days = (
                            _get_payment_snapshot_duration(
                                payment
                            ) or 0
                        )

                        if (
                            user.referred_by
                            and duration_days >= 30
                        ):
                            try:
                                completed_before = (
                                    await session.scalar(
                                        select(
                                            func.count(
                                                Payment.id
                                            )
                                        ).where(
                                            Payment.user_id
                                            == user.id,
                                            Payment.status
                                            == "completed",
                                            Payment.id
                                            != payment.id,
                                        )
                                    )
                                )
                                was_first = (
                                    completed_before == 0
                                )
                                bonus_ref = 3 if was_first else 1
                                bonus_user = 5 if was_first else 0

                                referrer = (
                                    await get_user_by_telegram_id(
                                        session,
                                        user.referred_by,
                                    )
                                )
                                if (
                                    referrer
                                    and bonus_ref > 0
                                ):
                                    if (
                                        referrer.referral_days
                                        and referrer.referral_days
                                        >= bonus_ref
                                    ):
                                        referrer.referral_days -= (
                                            bonus_ref
                                        )
                                    if (
                                        referrer.subscription_end
                                        and referrer
                                        .subscription_end
                                        > current_time
                                        and referrer
                                        .subscription_end.year
                                        < 2100
                                    ):
                                        referrer.subscription_end = (
                                            referrer
                                            .subscription_end
                                            - timedelta(
                                                days=bonus_ref,
                                            )
                                        )

                                    if (
                                        bonus_user > 0
                                        and user.subscription_end
                                        and user.subscription_end
                                        > current_time
                                        and user
                                        .subscription_end.year
                                        < 2100
                                    ):
                                        user.subscription_end = (
                                            user.subscription_end
                                            - timedelta(
                                                days=bonus_user
                                            )
                                        )
                            except Exception as e:
                                logger.error(
                                    "Chargeback referral "
                                    "rollback failed: %s",
                                    e, exc_info=True,
                                )

                        try:
                            await (
                                ProfileDeletionService
                                .delete_profiles_for_user(
                                    session,
                                    user.id,
                                    reason="chargeback_delete",
                                    background=True,
                                )
                            )
                        except Exception as e:
                            logger.error(
                                "Chargeback profile delete "
                                "failed: %s",
                                e, exc_info=True,
                            )

                    invalidate_user_cache(user.telegram_id)

                snapshot = _build_payment_snapshot(payment)

                try:
                    await AuditService.log_action(
                        session, admin_id=0,
                        action="PAYMENT_CHARGEBACK",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"YooKassa chargeback: "
                            f"transaction={transaction_id}, "
                            f"was_completed={was_completed}"
                        ),
                    )
                except Exception:
                    pass

                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_chargeback_alert_now(s, tid)
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _notify_client_chargeback_now(s)
                    ),
                )

                return True, "success"

        except Exception as e:
            logger.error(
                "Chargeback processing failed: %s",
                e, exc_info=True,
            )
            return False, "error"