# services/subscription.py
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from database.repositories.payments_repo import create_payment, get_payment_by_id, get_user_payments
from database.models import User, Payment
from config.settings import get_settings
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

class SubscriptionService:
    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
        """Проверить, есть ли у пользователя активная подписка"""
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user or user.is_banned:
            return False
        if not user.subscription_end:
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return user.subscription_end > now

    @staticmethod
    async def process_onboarding(
        session: AsyncSession,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        ref_id: int | None = None
    ) -> User:
        """Обработать онбординг нового пользователя"""
        user = await get_user_by_telegram_id(session, telegram_id)
        if user:
            return user
        referred_by = None
        if ref_id is not None and ref_id != telegram_id:
            ref_user = await get_user_by_telegram_id(session, ref_id)
            if ref_user:
                referred_by = ref_id
        return await create_user(session, telegram_id, username, first_name, referred_by)

    @staticmethod
    async def extend_subscription(
        session: AsyncSession,
        telegram_id: int,
        days: int
    ) -> Optional[User]:
        """Продлить подписку БЕЗ промежуточного commit()"""
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            logging.warning(f"extend_subscription: user {telegram_id} not found")
            return None
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if user.subscription_end and user.subscription_end > now:
            current_end = user.subscription_end
        else:
            current_end = now
        
        if days >= 36500:
            new_end = datetime(2100, 1, 1)
        else:
            new_end = current_end + timedelta(days=days)
        
        user.subscription_end = new_end
        logging.info(f"Extended subscription for user {telegram_id}: +{days} days, new end: {new_end}")
        return user

    @staticmethod
    async def get_expires_timestamp(user: User) -> Optional[int]:
        """🔥 FIX P3: Получить Unix timestamp для expiresAt в Amnezia API"""
        if not user.subscription_end:
            return None
        if user.subscription_end.year >= 2100:
            return None
        # 🔥 FIX P0 Timezone: Явно размечаем naive datetime как UTC
        return int(user.subscription_end.replace(tzinfo=timezone.utc).timestamp())

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession,
        payment_id: int
    ) -> bool:
        """
        Обработать успешный платёж в рамках ОДНОЙ транзакции.
        Защита от Race Condition двойных вебхуков через атомарный UPDATE.
        """
        # 🔥 FIX P1: Атомарный апдейт статуса платежа
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
            logging.info(f"Payment {payment_id} already processed or not found. Skipping.")
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
            logging.error(f"Payment {payment_id}: missing tariff or user")
            await session.rollback()
            return False
        
        await SubscriptionService.extend_subscription(session, user.telegram_id, tariff.duration_days)
        
        payments = await get_user_payments(session, user.id)
        completed_payments = [p for p in payments if p.status == 'completed']
        is_first_payment = len(completed_payments) == 1
        
        if is_first_payment and user.referred_by:
            referrer = await get_user_by_telegram_id(session, user.referred_by)
            if referrer:
                bonus_days = get_settings().REFERRAL_BONUS_DAYS
                await SubscriptionService.extend_subscription(session, referrer.telegram_id, bonus_days)
                new_referral_days = (referrer.referral_days or 0) + bonus_days
                referrer.referral_days = new_referral_days
                logging.info(
                    f"Referral bonus: user {user.telegram_id} first payment, "
                    f"referrer {referrer.telegram_id} got +{bonus_days} days"
                )
        
        user.last_payment_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        try:
            await session.commit()
            logging.info(
                f"Payment {payment_id} processed successfully: "
                f"user={user.telegram_id}, tariff={tariff.id}, days={tariff.duration_days}"
            )
            return True
        except Exception as e:
            await session.rollback()
            logging.error(f"Failed to commit payment {payment_id}: {e}", exc_info=True)
            return False