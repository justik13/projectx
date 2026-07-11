import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.payments_repo import get_payment_by_id, get_user_payments
from database.models import Payment
from services.subscription import SubscriptionService
from services.referral_service import ReferralService
from services.device_service import DeviceService

logger = logging.getLogger(__name__)


class PaymentService:
    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, payment_id: int
    ) -> dict:
        """
        Обрабатывает успешную оплату.
        Возвращает dict с результатом:
        {
            "success": bool,
            "disabled_devices": int,   # сколько устройств было приостановлено
            "restored_devices": int,   # сколько устройств было восстановлено
        }
        """
        result_dict = {
            "success": False,
            "disabled_devices": 0,
            "restored_devices": 0,
        }

        stmt = (
            update(Payment)
            .where(Payment.id == payment_id, Payment.status == 'pending')
            .values(status='completed', paid_at=datetime.now(timezone.utc).replace(tzinfo=None))
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            result_dict["success"] = True  # уже обработано
            return result_dict

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
            return result_dict

        old_device_limit = user.device_limit
        new_device_limit = getattr(tariff, 'device_limit', old_device_limit)

        # Продлеваем подписку и обновляем device_limit
        await SubscriptionService.extend_subscription(
            session, user.telegram_id, tariff.duration_days,
            new_device_limit=new_device_limit,
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
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to commit payment {payment_id}: {e}", exc_info=True)
            return result_dict

        # === ЛОГИКА ДАУНГРЕЙДА / АПГРЕЙДА СЛОТОВ ===
        if new_device_limit < old_device_limit:
            # Даунгрейд — приостанавливаем лишние устройства
            disabled = await DeviceService.enforce_device_limit(
                session, user, new_device_limit
            )
            result_dict["disabled_devices"] = disabled
        elif new_device_limit > old_device_limit:
            # Апгрейд — восстанавливаем приостановленные устройства
            restored = await DeviceService.restore_devices_up_to_limit(
                session, user, new_device_limit
            )
            result_dict["restored_devices"] = restored

        result_dict["success"] = True
        return result_dict