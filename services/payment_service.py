import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.payments_repo import get_user_payments, get_payment_by_id
from database.models import Payment
from services.subscription import SubscriptionService
from services.referral_service import ReferralService
from services.platega_client import PlategaClient
from services.audit_service import AuditService
from config.settings import get_settings

logger = logging.getLogger(__name__)


class PaymentService:

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, payment_id: int
    ) -> bool:
        """
        Обрабатывает успешную оплату. Возвращает True при успехе.
        
        🔥 ИСПРАВЛЕНО #16: Referral bonus logic
        Раньше: is_first_payment = len(completed_payments) == 1
        Проблема: если первая оплата была Stars (failed), а вторая SBP (completed),
        бонус начислялся только за SBP. Если первая failed, вторая completed — бонус начислялся.
        
        Теперь: is_first_payment проверяется по paid_at IS NOT NULL.
        Это корректно определяет "первая УСПЕШНАЯ оплата".
        """
        try:
            async with session.begin_nested() as savepoint:
                stmt = (
                    update(Payment)
                    .where(Payment.id == payment_id, Payment.status == 'pending')
                    .values(
                        status='completed',
                        paid_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    )
                )
                result = await session.execute(stmt)
                if result.rowcount == 0:
                    await savepoint.commit()
                    return True

                result = await session.execute(
                    select(Payment)
                    .options(selectinload(Payment.user), selectinload(Payment.tariff))
                    .where(Payment.id == payment_id)
                )
                payment = result.scalar_one()
                tariff = payment.tariff
                user = payment.user

                if not tariff or not user:
                    logger.error(f"Payment {payment_id}: missing tariff or user")
                    await savepoint.rollback()
                    return False

                new_device_limit = getattr(tariff, 'device_limit', user.device_limit)
                try:
                    await SubscriptionService.extend_subscription(
                        session, user.telegram_id, tariff.duration_days,
                        new_device_limit=new_device_limit,
                        new_tariff_id=tariff.id,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to extend subscription for payment {payment_id}: {e}",
                        exc_info=True
                    )
                    await savepoint.rollback()
                    return False

                # 🔥 ИСПРАВЛЕНО #16: Проверка по paid_at IS NOT NULL
                payments = await get_user_payments(session, user.id)
                # Считаем только платежи с paid_at != None (успешные оплаты)
                successful_payments = [p for p in payments if p.paid_at is not None]
                is_first_payment = len(successful_payments) == 1

                if is_first_payment and user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session, user.telegram_id, user.referred_by
                        )
                    except Exception as e:
                        logger.warning(
                            f"Referral bonus failed for payment {payment_id}: {e}"
                        )

                user.last_payment_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await savepoint.commit()
                await session.commit()

                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="PAYMENT_SUCCESS",
                        target_type="Payment", target_id=payment_id,
                        details=f"user={user.telegram_id}, amount={payment.amount} {payment.currency}"
                    )
                except Exception as e:
                    logger.error(f"Failed to log payment success to audit: {e}")

                logger.info(
                    f"Payment {payment_id} processed successfully for user {user.telegram_id}"
                )
                return True

        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to process payment {payment_id}: {e}", exc_info=True)
            return False

    @staticmethod
    async def create_platega_payment(
        session: AsyncSession, user_id: int, tariff_id: int,
        amount: float, telegram_id: int, bot_username: str
    ) -> tuple:
        from database.repositories.payments_repo import create_payment
        settings = get_settings()

        payment = await create_payment(
            session=session, user_id=user_id, tariff_id=tariff_id,
            amount=int(amount), currency="RUB"
        )

        description = f"Оплата подписки. TgId:{telegram_id} UserId:{user_id}"
        clean_username = bot_username.lstrip("@")
        return_url = settings.PLATEGA_RETURN_URL.format(bot_username=clean_username)
        failed_url = settings.PLATEGA_FAILED_URL.format(bot_username=clean_username)
        payload = f"payment_{payment.id}"

        client = PlategaClient()
        transaction = await client.create_transaction(
            amount=amount, currency="RUB", description=description,
            return_url=return_url, failed_url=failed_url, payload=payload
        )

        if not transaction:
            payment.status = "failed"
            await session.commit()
            return payment, None

        payment.external_id = transaction.get("transactionId")
        payment.payment_url = transaction.get("redirect")
        payment.payment_method = transaction.get("paymentMethod", "SBPQR")
        await session.commit()
        return payment, None

    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession, transaction_id: str,
        status: str, payload: str
    ) -> tuple[bool, str]:
        stmt = (
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.external_id == transaction_id)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(f"Platega callback: payment not found for {transaction_id}")
            return False, "not_found"

        logger.info(f"Platega callback: payment {payment.id} status={status}")

        if status == "CONFIRMED":
            success = await PaymentService.handle_successful_payment(session, payment.id)
            return success, "success" if success else "error"

        elif status == "CANCELED":
            if payment.status == "cancelled":
                logger.info(f"Platega callback: payment {payment.id} already cancelled")
                return True, "already_processed"
            payment.status = "cancelled"
            await session.commit()
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CANCELLED",
                    target_type="Payment", target_id=payment.id,
                    details=f"Platega callback: transaction={transaction_id}"
                )
            except Exception as e:
                logger.error(f"Failed to log payment cancellation to audit: {e}")
            return True, "success"

        elif status == "CHARGEBACKED":
            if payment.status == "refunded":
                logger.info(f"Platega callback: payment {payment.id} already refunded")
                return True, "already_processed"
            payment.status = "refunded"
            await session.commit()
            logger.warning(f"Chargeback for payment {payment.id}")
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CHARGEBACK",
                    target_type="Payment", target_id=payment.id,
                    details=f"Platega chargeback: transaction={transaction_id}"
                )
            except Exception as e:
                logger.error(f"Failed to log chargeback to audit: {e}")
            return True, "success"

        logger.warning(f"Unknown Platega status: {status}")
        return False, "error"

    @staticmethod
    async def check_platega_payment(session: AsyncSession, payment_id: int) -> bool:
        payment = await get_payment_by_id(session, payment_id)
        if not payment or not payment.external_id:
            return False
        if payment.status != "pending":
            return payment.status == "completed"

        client = PlategaClient()
        status_data = await client.check_status(payment.external_id)
        if not status_data:
            return False

        status = status_data.get("status")
        if status == "CONFIRMED":
            return await PaymentService.handle_successful_payment(session, payment.id)
        elif status == "CANCELED":
            payment.status = "cancelled"
            await session.commit()
            return False
        return False