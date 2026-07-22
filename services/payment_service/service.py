import logging
from datetime import timedelta

from sqlalchemy import func, select, update
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
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.platega_client import PlategaClient
from services.profile_deletion_service import ProfileDeletionService
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
    _expected_payment_amount,
    _get_redis,
    _to_decimal,
)

logger = logging.getLogger(__name__)


class PaymentService:

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        """
        Обрабатывает успешный платёж.

        Безопасная логика:
        1. Блокируем строку платежа.
        2. Проверяем статус.
        3. Проверяем пользователя.
        4. Проверяем тариф.
        5. Проверяем сумму.
        6. Только затем выдаём доступ.

        Если что-то не так, платёж переводится в
        requires_manual_review, а админ получает алерт.
        """
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
                user_lock_key,
                timeout=30,
                blocking_timeout=15,
            )
            acquired = await redis_lock.acquire()
            if not acquired:
                logger.warning(
                    "Payment %s: failed to acquire Redis bonus lock. "
                    "Continuing with DB row lock only.",
                    payment_id,
                )
        except Exception as e:
            logger.warning(
                "Payment %s: Redis unavailable for bonus lock: %s. "
                "Continuing with DB row lock only.",
                payment_id,
                e,
            )
            redis_lock = None
            acquired = False

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "completed":
                    logger.info(
                        "Payment %s already completed "
                        "(idempotent)",
                        payment_id,
                    )
                    return True, "already_processed"

                if payment.status == "cancelled":
                    snapshot = _build_payment_snapshot(payment)
                    await AuditService.log_action(
                        session,
                        admin_id=0,
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
                    logger.warning(
                        "Payment %s is refunded, cannot grant "
                        "access",
                        payment_id,
                    )
                    return False, "refunded"

                if payment.status == "requires_manual_review":
                    logger.info(
                        "Payment %s already in manual review",
                        payment_id,
                    )
                    return False, "manual_review"

                if payment.status == "failed":
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = "status_failed"
                    await session.flush()
                    snapshot = _build_payment_snapshot(payment)
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_MANUAL_REVIEW",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            "reason=status_failed, "
                            "source=handle_successful_payment"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot: (
                            _send_manual_review_alert_now(
                                s,
                                "status_failed",
                                "handle_successful_payment",
                            )
                        ),
                    )
                    return False, "manual_review"

                user = payment.user
                tariff = payment.tariff

                manual_review_reason = None

                if not user or not tariff:
                    manual_review_reason = (
                        "missing_tariff_or_user"
                    )
                elif user.is_deleted or user.is_banned:
                    manual_review_reason = "banned_or_deleted"
                elif not tariff.is_active:
                    manual_review_reason = "inactive_tariff"
                else:
                    expected_amount = (
                        _expected_payment_amount(payment)
                    )
                    if expected_amount is None:
                        manual_review_reason = "amount_missing"
                    elif payment.amount != expected_amount:
                        manual_review_reason = "amount_mismatch"
                        logger.error(
                            "Payment %s amount mismatch: "
                            "stored=%s, expected=%s, currency=%s",
                            payment_id,
                            payment.amount,
                            expected_amount,
                            payment.currency,
                        )

                if manual_review_reason:
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = (
                        manual_review_reason
                    )
                    await session.flush()
                    snapshot = _build_payment_snapshot(payment)
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_MANUAL_REVIEW",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"reason={manual_review_reason}, "
                            f"source=handle_successful_payment"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot, r=manual_review_reason: (
                            _send_manual_review_alert_now(
                                s,
                                r,
                                "handle_successful_payment",
                            )
                        ),
                    )
                    return False, "manual_review"

                # Помечаем платёж как completed.
                payment.status = "completed"
                payment.paid_at = now_utc()
                await session.flush()

                # Выдаём доступ.
                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        tariff.duration_days,
                        new_device_limit=tariff.device_limit,
                        new_tariff_id=tariff.id,
                    )
                except ValueError as e:
                    logger.error(
                        "Payment %s: subscription extend "
                        "failed: %s",
                        payment_id,
                        e,
                    )
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = (
                        "device_limit_exceeded"
                    )
                    await session.flush()
                    snapshot = _build_payment_snapshot(payment)
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_MANUAL_REVIEW",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            "reason=device_limit_exceeded, "
                            "source=handle_successful_payment_extend"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot: (
                            _send_manual_review_alert_now(
                                s,
                                "device_limit_exceeded",
                                "handle_successful_payment_extend",
                            )
                        ),
                    )
                    return False, "manual_review"
                except Exception as e:
                    logger.error(
                        "Payment %s: unexpected extend error: %s",
                        payment_id,
                        e,
                        exc_info=True,
                    )
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = "status_failed"
                    await session.flush()
                    snapshot = _build_payment_snapshot(payment)
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_MANUAL_REVIEW",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            "reason=status_failed, "
                            "source=handle_successful_payment_extend"
                        ),
                    )
                    queue_post_commit_task(
                        session,
                        lambda s=snapshot: (
                            _send_manual_review_alert_now(
                                s,
                                "status_failed",
                                "handle_successful_payment_extend",
                            )
                        ),
                    )
                    return False, "manual_review"

                # Реферальные бонусы.
                payments = await get_user_payments(
                    session,
                    user.id,
                )
                successful_payments = [
                    p
                    for p in payments
                    if p.status == "completed"
                ]
                is_first_payment = len(successful_payments) == 1

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=tariff.duration_days,
                        )
                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed for payment "
                            "%s: %s",
                            payment_id,
                            e,
                        )

                user.last_payment_at = now_utc()
                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
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
                        "Failed to log payment success to "
                        "audit: %s",
                        e,
                    )

                logger.info(
                    "Payment %s processed successfully for "
                    "user %s",
                    payment_id,
                    user.telegram_id,
                )
                return True, "success"

        except Exception as e:
            logger.error(
                "Failed to process payment %s: %s",
                payment_id,
                e,
                exc_info=True,
            )
            return False, "error"
        finally:
            if redis_lock is not None and acquired:
                try:
                    await redis_lock.release()
                except Exception:
                    pass

    @staticmethod
    async def force_grant_payment(
        session: AsyncSession,
        payment_id: int,
        admin_id: int,
    ) -> tuple:
        """
        Ручная выдача платежа админом.

        Разрешено только для безопасных статусов:
        - pending
        - cancelled
        - failed
        - requires_manual_review

        Запрещено для:
        - completed
        - refunded

        Также запрещено выдавать доступ заблокированному
        пользователю.
        """
        allowed_statuses = MANUAL_GRANT_ALLOWED_STATUSES

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )
                if not payment:
                    return False, "Платёж не найден"

                if payment.status == "completed":
                    return False, "Платёж уже выдан"

                if payment.status == "refunded":
                    return False, (
                        "Платёж возвращён, выдача запрещена"
                    )

                if payment.status not in allowed_statuses:
                    return False, "Недопустимый статус платежа"

                user = payment.user
                tariff = payment.tariff

                if not user:
                    return False, "Пользователь не найден"
                if user.is_deleted:
                    return False, "Пользователь удалён"
                if user.is_banned:
                    return False, "Пользователь заблокирован"
                if not tariff:
                    return False, "Тариф не найден"

                payment.status = "completed"
                if not payment.paid_at:
                    payment.paid_at = now_utc()
                await session.flush()

                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        tariff.duration_days,
                        new_device_limit=tariff.device_limit,
                        new_tariff_id=tariff.id,
                    )
                except ValueError as e:
                    logger.error(
                        "force_grant: extend failed for payment "
                        "%s: %s",
                        payment_id,
                        e,
                    )
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = (
                        "device_limit_exceeded"
                    )
                    await session.flush()
                    return False, "Превышен лимит устройств"
                except Exception as e:
                    logger.error(
                        "force_grant: unexpected extend error "
                        "for payment %s: %s",
                        payment_id,
                        e,
                        exc_info=True,
                    )
                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = "status_failed"
                    await session.flush()
                    return False, f"Ошибка продления: {e}"

                payments = await get_user_payments(
                    session,
                    user.id,
                )
                successful_payments = [
                    p
                    for p in payments
                    if p.status == "completed"
                ]
                is_first_payment = len(successful_payments) == 1

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=tariff.duration_days,
                        )
                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed for manual "
                            "grant %s: %s",
                            payment_id,
                            e,
                        )

                user.last_payment_at = now_utc()
                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=admin_id,
                        action="MANUAL_GRANT",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"Admin {admin_id} manually granted "
                            f"payment {payment_id} for user "
                            f"{user.telegram_id}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        "force_grant: audit failed: %s",
                        e,
                    )

                return True, "ok"

        except Exception as e:
            logger.error(
                "force_grant_payment failed: %s",
                e,
                exc_info=True,
            )
            return False, f"Ошибка БД: {e}"

    @staticmethod
    async def create_platega_payment(
        session: AsyncSession,
        user_id: int,
        tariff_id: int,
        amount: float,
        telegram_id: int,
        bot_username: str,
    ) -> tuple:
        """
        Создаёт платёж через платёжную систему.

        Важно:
        - сумма в БД хранится как Decimal;
        - описание платежа не содержит личных ID;
        - payload содержит только ID платежа;
        - при ошибке провайдера платёж остаётся в БД со статусом failed;
        - если провайдер не вернул ID транзакции или платёжную ссылку,
          платёж считается failed.
        """
        from config.settings import get_settings

        settings = get_settings()

        decimal_amount = _to_decimal(amount)
        if decimal_amount is None:
            logger.error(
                "create_platega_payment: invalid amount %s",
                amount,
            )
            return None, None

        payment = await create_payment(
            session=session,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=decimal_amount,
            currency="RUB",
        )

        description = f"Payment #{payment.id}"
        clean_username = bot_username.lstrip("@")
        return_url = settings.PLATEGA_RETURN_URL.format(
            bot_username=clean_username,
        )
        failed_url = settings.PLATEGA_FAILED_URL.format(
            bot_username=clean_username,
        )
        payload = f"payment_{payment.id}"

        client = PlategaClient()
        transaction = await client.create_transaction(
            amount=float(decimal_amount),
            currency="RUB",
            description=description,
            return_url=return_url,
            failed_url=failed_url,
            payload=payload,
        )

        if not transaction:
            payment.status = "failed"
            try:
                await session.flush()
            except Exception:
                pass
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_FAILED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"user={user_id}, "
                        f"amount={decimal_amount} RUB, "
                        f"payment provider create_transaction failed"
                    ),
                )
            except Exception as e:
                logger.error(
                    "Failed to log payment failure to audit: %s",
                    e,
                )
            return None, None

        external_id = (
            transaction.get("transactionId")
            or transaction.get("id")
            or transaction.get("paymentId")
            or transaction.get("invoiceId")
        )
        payment_url = (
            transaction.get("redirect")
            or transaction.get("redirectUrl")
            or transaction.get("paymentUrl")
            or transaction.get("url")
            or transaction.get("link")
        )

        if external_id is None or not payment_url:
            payment.status = "failed"
            try:
                await session.flush()
            except Exception:
                pass
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_FAILED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"user={user_id}, "
                        f"amount={decimal_amount} RUB, "
                        f"missing transaction id or payment url"
                    ),
                )
            except Exception as e:
                logger.error(
                    "Failed to log payment failure to audit: %s",
                    e,
                )
            return None, None

        payment.external_id = str(external_id)
        payment.payment_url = str(payment_url)

        raw_payment_method = transaction.get("paymentMethod")
        payment.payment_method = (
            str(raw_payment_method)
            if raw_payment_method is not None
            else "SBPQR"
        )

        return payment, None

    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession,
        transaction_id: str,
        status: str,
        payload: str,
        callback_amount: float | None = None,
        callback_payload: str | None = None,
        callback_currency: str | None = None,
    ) -> tuple:
        stmt = (
            select(Payment)
            .options(
                selectinload(Payment.user),
                selectinload(Payment.tariff),
            )
            .where(Payment.external_id == transaction_id)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(
                "Payment provider callback: payment not found "
                "for transaction=%s",
                transaction_id,
            )
            return False, "not_found"

        logger.info(
            "Payment provider callback: payment %s status=%s",
            payment.id,
            status,
        )

        if status == "CONFIRMED":
            if payment.status == "completed":
                logger.info(
                    "Payment provider callback: payment %s already "
                    "completed, idempotent success for "
                    "transaction=%s",
                    payment.id,
                    transaction_id,
                )
                return True, "already_processed"

            if payment.status == "cancelled":
                snapshot = _build_payment_snapshot(payment)
                await AuditService.log_action(
                    session,
                    admin_id=0,
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
                logger.warning(
                    "Payment provider callback: CONFIRMED received "
                    "for refunded payment %s",
                    payment.id,
                )
                return False, "refunded"

            # ── Верификация суммы ──────────────────────────
            if callback_amount is None:
                client = PlategaClient()
                status_data = await client.check_status(
                    transaction_id,
                )
                if (
                    status_data
                    and status_data.get("amount") is not None
                ):
                    recovered = _to_decimal(
                        status_data["amount"]
                    )
                    if recovered is not None:
                        callback_amount = float(recovered)
                        logger.info(
                            "Payment provider callback: amount "
                            "recovered via API check_status: %s "
                            "for transaction=%s",
                            callback_amount,
                            transaction_id,
                        )

            if callback_amount is None:
                logger.error(
                    "Payment provider callback: amount not "
                    "provided and API verification failed for "
                    "transaction=%s",
                    transaction_id,
                )
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_missing",
                    source="platega_callback",
                )
                return False, "manual_review"

            callback_decimal = _to_decimal(callback_amount)
            if callback_decimal is None:
                logger.error(
                    "Payment provider callback: invalid callback "
                    "amount %s for transaction=%s",
                    callback_amount,
                    transaction_id,
                )
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_mismatch",
                    source="platega_callback",
                )
                return False, "manual_review"

            if payment.amount != callback_decimal:
                logger.error(
                    "Payment provider amount mismatch: DB=%s, "
                    "callback=%s, payment_id=%s, transaction=%s",
                    payment.amount,
                    callback_decimal,
                    payment.id,
                    transaction_id,
                )
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_mismatch",
                    source="platega_callback",
                )
                return False, "manual_review"

            # ── Верификация валюты ─────────────────────────
            if callback_currency:
                callback_currency_norm = str(
                    callback_currency
                ).upper()
                payment_currency_norm = str(
                    payment.currency
                ).upper()
                if (
                    payment_currency_norm
                    != callback_currency_norm
                ):
                    logger.error(
                        "Payment provider currency mismatch: "
                        "DB=%s, callback=%s, payment_id=%s, "
                        "transaction=%s",
                        payment.currency,
                        callback_currency,
                        payment.id,
                        transaction_id,
                    )
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "currency_mismatch",
                        source="platega_callback",
                    )
                    return False, "manual_review"

            expected_payload = f"payment_{payment.id}"
            # Пустой payload считаем отсутствующим.
            if (
                callback_payload not in (None, "")
                and callback_payload != expected_payload
            ):
                logger.error(
                    "Payment provider payload mismatch: "
                    "expected=%s, callback=%s, payment_id=%s",
                    expected_payload,
                    callback_payload,
                    payment.id,
                )
                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "payload_mismatch",
                    source="platega_callback",
                )
                return False, "manual_review"

            success, result_code = (
                await PaymentService.handle_successful_payment(
                    session,
                    payment.id,
                )
            )
            return success, result_code

        elif status == "CANCELED":
            if payment.status == "refunded":
                logger.info(
                    "Payment provider callback: payment %s already "
                    "refunded, ignoring CANCELED",
                    payment.id,
                )
                return True, "already_processed"

            if payment.status == "cancelled":
                logger.info(
                    "Payment provider callback: payment %s already "
                    "cancelled",
                    payment.id,
                )
                return True, "already_processed"

            if payment.status == "completed":
                logger.error(
                    "Payment provider callback: CANCELED received "
                    "for completed payment %s",
                    payment.id,
                )
                snapshot = _build_payment_snapshot(payment)
                await AuditService.log_action(
                    session,
                    admin_id=0,
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
                            s, tid
                        )
                    ),
                )
                return True, "manual_review"

            payment.status = "cancelled"
            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_CANCELLED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"Payment provider callback: "
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
            except Exception as e:
                logger.error(
                    "Failed to log payment cancellation to "
                    "audit: %s",
                    e,
                )
            return True, "success"

        elif status == "CHARGEBACKED":
            return await PaymentService._process_chargeback(
                session,
                payment.id,
                transaction_id,
            )

        logger.warning(
            "Unknown payment provider status: %s",
            status,
        )
        return False, "error"

    @staticmethod
    async def check_platega_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        payment = await get_payment_by_id(
            session,
            payment_id,
        )
        if not payment or not payment.external_id:
            return False, "not_found"

        if payment.status == "completed":
            return True, "success"

        client = PlategaClient()

        #
        # ВАЖНО:
        #
        # Если платёж в БД уже cancelled, мы всё равно должны
        # проверить статус у провайдера.
        #
        # Иначе сценарий "пользователь отменил, но успел оплатить"
        # не будет обработан кнопкой "Я оплатил".
        #
        if payment.status == "cancelled":
            status_data = await client.check_status(
                payment.external_id,
            )
            if status_data:
                provider_status = status_data.get("status")

                if provider_status == "CONFIRMED":
                    #
                    # Верификация суммы.
                    # Если amount отсутствует → manual_review.
                    #
                    callback_amount = status_data.get("amount")

                    if callback_amount is None:
                        logger.error(
                            "check_platega_payment: cancelled "
                            "payment %s CONFIRMED but amount "
                            "missing from provider. "
                            "Moving to manual review.",
                            payment.id,
                        )
                        await PaymentService._set_manual_review(
                            session,
                            payment.id,
                            "amount_missing",
                            source=(
                                "check_platega_payment_cancelled"
                            ),
                        )
                        return False, "manual_review"

                    callback_decimal = _to_decimal(
                        callback_amount
                    )
                    if (
                        callback_decimal is None
                        or payment.amount != callback_decimal
                    ):
                        await PaymentService._set_manual_review(
                            session,
                            payment.id,
                            "amount_mismatch",
                            source=(
                                "check_platega_payment_cancelled"
                            ),
                        )
                        return False, "manual_review"

                    #
                    # Верификация валюты.
                    # ВНУТРИ if provider_status == "CONFIRMED".
                    #
                    callback_currency = status_data.get(
                        "currency"
                    )
                    if callback_currency:
                        callback_currency_norm = str(
                            callback_currency
                        ).upper()
                        payment_currency_norm = str(
                            payment.currency
                        ).upper()
                        if (
                            payment_currency_norm
                            != callback_currency_norm
                        ):
                            await PaymentService._set_manual_review(
                                session,
                                payment.id,
                                "currency_mismatch",
                                source=(
                                    "check_platega_payment_cancelled"
                                ),
                            )
                            return False, "manual_review"

                    return (
                        await PaymentService
                        .handle_successful_payment(
                            session,
                            payment.id,
                        )
                    )

                if provider_status == "CHARGEBACKED":
                    return (
                        await PaymentService._process_chargeback(
                            session,
                            payment.id,
                            payment.external_id,
                        )
                    )

            return False, "cancelled"

        if payment.status == "requires_manual_review":
            return False, "manual_review"

        if payment.status == "refunded":
            return False, "refunded"

        if payment.status != "pending":
            return False, "invalid_status"

        status_data = await client.check_status(
            payment.external_id,
        )
        if not status_data:
            return False, "api_error"

        status = status_data.get("status")

        if status == "CONFIRMED":
            callback_amount = status_data.get("amount")
            if callback_amount is not None:
                callback_decimal = _to_decimal(callback_amount)
                if (
                    callback_decimal is None
                    or payment.amount != callback_decimal
                ):
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "amount_mismatch",
                        source="check_platega_payment",
                    )
                    return False, "manual_review"

            callback_currency = status_data.get("currency")
            if callback_currency:
                callback_currency_norm = str(
                    callback_currency
                ).upper()
                payment_currency_norm = str(
                    payment.currency
                ).upper()
                if (
                    payment_currency_norm
                    != callback_currency_norm
                ):
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "currency_mismatch",
                        source="check_platega_payment",
                    )
                    return False, "manual_review"

            success, result_code = (
                await PaymentService.handle_successful_payment(
                    session,
                    payment.id,
                )
            )
            return success, result_code

        elif status == "CANCELED":
            if payment.status == "completed":
                logger.error(
                    "check_platega_payment: CANCELED received "
                    "for completed payment %s",
                    payment.id,
                )
                snapshot = _build_payment_snapshot(payment)
                transaction_id = payment.external_id or "—"
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_CANCEL_AFTER_COMPLETED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"check_platega_payment: "
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_cancel_after_completed_alert_now(
                            s, tid
                        )
                    ),
                )
                return False, "manual_review"

            if payment.status != "cancelled":
                payment.status = "cancelled"
                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CANCELLED",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            "check_platega_payment: "
                            "status=CANCELED, "
                            f"user={payment.user_id}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to log payment cancellation "
                        "to audit: %s",
                        e,
                    )
            return False, "cancelled"

        elif status == "CHARGEBACKED":
            return await PaymentService._process_chargeback(
                session,
                payment.id,
                payment.external_id,
            )

        return False, "pending"

    @staticmethod
    async def _set_manual_review(
        session: AsyncSession,
        payment_id: int,
        reason: str,
        source: str,
    ) -> tuple:
        """
        Переводит платёж в статус requires_manual_review.
        Используется, когда платёж нельзя безопасно обработать
        автоматически.
        """
        stmt = (
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status.in_(
                    [
                        "pending",
                        "failed",
                        "cancelled",
                    ]
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
            session,
            payment_id,
        )
        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session,
            admin_id=0,
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

    @staticmethod
    async def _process_chargeback(
        session: AsyncSession,
        payment_id: int,
        transaction_id: str,
    ) -> tuple:
        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )
                if not payment:
                    return False, "not_found"

                if payment.status == "refunded":
                    logger.info(
                        "Payment provider callback: payment %s "
                        "already refunded",
                        payment.id,
                    )
                    return True, "already_processed"

                was_completed = payment.status == "completed"
                payment.status = "refunded"
                payment.manual_review_reason = None
                await session.flush()

                user = payment.user

                if user:
                    current_time = now_utc()

                    if was_completed:
                        # Отзываем доступ только если платёж реально
                        # был completed.
                        user.subscription_end = current_time
                        user.current_tariff_id = None
                        user.device_limit = 0
                        await session.flush()

                        # Откатываем реферальные бонусы.
                        tariff = payment.tariff
                        if (
                            user.referred_by
                            and tariff
                            and tariff.duration_days >= 30
                        ):
                            try:
                                completed_before = (
                                    await session.scalar(
                                        select(
                                            func.count(
                                                Payment.id
                                            )
                                        )
                                        .where(
                                            Payment.user_id
                                            == user.id,
                                            Payment.status
                                            == "completed",
                                            Payment.id
                                            != payment.id,
                                        )
                                    )
                                )
                                was_first_payment = (
                                    completed_before == 0
                                )

                                if was_first_payment:
                                    bonus_referrer = 3
                                    bonus_user = 5
                                else:
                                    bonus_referrer = 1
                                    bonus_user = 0

                                # Откатываем бонус реферера.
                                referrer = (
                                    await get_user_by_telegram_id(
                                        session,
                                        user.referred_by,
                                    )
                                )
                                if (
                                    referrer
                                    and bonus_referrer > 0
                                ):
                                    if (
                                        referrer.referral_days
                                        and referrer.referral_days
                                        >= bonus_referrer
                                    ):
                                        referrer.referral_days -= (
                                            bonus_referrer
                                        )

                                    # Не вычитаем дни из вечной
                                    # подписки.
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
                                                days=(
                                                    bonus_referrer
                                                ),
                                            )
                                        )
                                        logger.info(
                                            "Chargeback: rolled "
                                            "back referrer bonus "
                                            "for %s",
                                            referrer.telegram_id,
                                        )

                                # Откатываем бонус самого
                                # пользователя, если это была
                                # первая покупка.
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
                                    logger.info(
                                        "Chargeback: rolled back "
                                        "first-purchase user "
                                        "bonus for %s",
                                        user.telegram_id,
                                    )
                            except Exception as e:
                                logger.error(
                                    "Chargeback: failed to "
                                    "rollback referral "
                                    "bonuses: %s",
                                    e,
                                    exc_info=True,
                                )

                        # Удаляем устройства пользователя.
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
                                "Chargeback: failed to delete "
                                "profiles for user %s: %s",
                                user.id,
                                e,
                                exc_info=True,
                            )
                    else:
                        logger.warning(
                            "Chargeback for non-completed "
                            "payment %s: access was not revoked",
                            payment.id,
                        )

                    invalidate_user_cache(user.telegram_id)

                logger.warning(
                    "CHARGEBACK processed: user %s, payment %s. "
                    "was_completed=%s",
                    payment.user_id,
                    payment.id,
                    was_completed,
                )

                snapshot = _build_payment_snapshot(payment)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CHARGEBACK",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"Payment provider chargeback: "
                            f"transaction={transaction_id}, "
                            f"user={payment.user_id}, "
                            f"was_completed={was_completed}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        "Failed to log chargeback to audit: %s",
                        e,
                    )

                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_chargeback_alert_now(s, tid)
                    ),
                )

                # НОВОЕ: уведомление клиента при chargeback.
                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _notify_client_chargeback_now(s)
                    ),
                )

                return True, "success"

        except Exception as e:
            logger.error(
                "Chargeback processing failed for payment %s: %s",
                payment_id,
                e,
                exc_info=True,
            )
            return False, "error"