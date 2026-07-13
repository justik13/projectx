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
from config.settings import get_settings

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

    @staticmethod
    async def create_platega_payment(
        session: AsyncSession,
        user_id: int,
        tariff_id: int,
        amount: float,
        telegram_id: int
    ) -> tuple[Payment | None, dict | None]:
        """Создает платеж через Platega.io"""
        from database.repositories.payments_repo import create_payment
        
        settings = get_settings()
        
        # Создаем запись в БД
        payment = await create_payment(
            session=session,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=int(amount),
            currency="RUB"
        )
        
        # Формируем description с Telegram ID (требование Platega)
        description = f"Оплата подписки VPN. TgId:{telegram_id} UserId:{user_id}"
        
        # Формируем return URL
        bot_username = "your_bot_username"  # TODO: получить из bot.get_me()
        return_url = settings.PLATEGA_RETURN_URL.format(bot_username=bot_username)
        failed_url = settings.PLATEGA_FAILED_URL.format(bot_username=bot_username)
        
        # Payload для идентификации
        payload = f"payment_{payment.id}"
        
        # Создаем транзакцию в Platega
        client = PlategaClient()
        transaction = await client.create_transaction(
            amount=amount,
            currency="RUB",
            description=description,
            return_url=return_url,
            failed_url=failed_url,
            payload=payload
        )
        
        if not transaction:
            payment.status = "failed"
            await session.commit()
            return payment, None
        
        # Сохраняем данные транзакции
        payment.external_id = transaction.get("transactionId")
        payment.payment_url = transaction.get("redirect")
        payment.payment_method = transaction.get("paymentMethod", "SBPQR")
        await session.commit()
        
        # Получаем QR-код
        qr_data = await client.get_qr_code(payment.external_id)
        if qr_data:
            payment.qr_code = qr_data.get("qr")
            await session.commit()
        
        return payment, qr_data

    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession,
        transaction_id: str,
        status: str,
        payload: str
    ) -> bool:
        """Обрабатывает callback от Platega.io"""
        # Находим платеж по external_id
        stmt = (
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.external_id == transaction_id)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if not payment:
            logger.warning(f"Platega callback: payment not found for {transaction_id}")
            return False
        
        logger.info(f"Platega callback: payment {payment.id} status={status}")
        
        if status == "CONFIRMED":
            # Оплата успешна
            return await PaymentService.handle_successful_payment(session, payment.id)
        
        elif status == "CANCELED":
            # Оплата отменена
            payment.status = "cancelled"
            await session.commit()
            return True
        
        elif status == "CHARGEBACKED":
            # Возврат средств
            payment.status = "refunded"
            await session.commit()
            logger.warning(f"Chargeback for payment {payment.id}")
            return True
        
        return False

    @staticmethod
    async def check_platega_payment(session: AsyncSession, payment_id: int) -> bool:
        """Проверяет статус платежа в Platega (для кнопки 'Проверить оплату')"""
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