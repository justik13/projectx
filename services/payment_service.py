import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.payments_repo import get_user_payments
from database.models import Payment
from services.subscription import SubscriptionService
from services.referral_service import ReferralService

logger = logging.getLogger(__name__)


class PaymentService:
    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, payment_id: int
    ) -> bool:
        """Обрабатывает успешную оплату. Возвращает True при успехе."""
        stmt = (
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == 'pending')
            .values(status='completed', paid_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            return True  # уже обработано

        result = await session.execute(
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.id == payment_id)
        )
        payment = result.scalar_one()
        tariff = payment.tariff
        user = payment.user

        if not tariff or not user:
            await session.rollback()
            return False

        new_device_limit = getattr(tariff, 'device_limit', user.device_limit)

        await SubscriptionService.extend_subscription(
            session,
            user.telegram_id,
            tariff.duration_days,
            new_device_limit=new_device_limit,
            new_tariff_id=tariff.id,
        )

        # Реферальный бонус за первую оплату
        payments = await get_user_payments(session, user.id)
        completed_payments = [p for p in payments if p.status == 'completed']
        is_first_payment = len(completed_payments) == 1

        if is_first_payment and user.referred_by:
            await ReferralService.process_bonus(session, user.telegram_id, user.referred_by)

        user.last_payment_at = datetime.now(timezone.utc).replace(tzinfo=None)

        try:
            await session.commit()
            return True
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to commit payment {payment_id}: {e}", exc_info=True)
            return False