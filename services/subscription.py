from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user, extend_subscription
from database.repositories.payments_repo import create_payment, mark_payment_as_paid, get_user_payments, get_last_payment
from database.models import User, Payment
from config.settings import get_settings
from datetime import datetime
from typing import Optional

class SubscriptionService:
    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user or user.is_banned or not user.subscription_end or user.subscription_end < datetime.utcnow():
            return False
        return True

    @staticmethod
    async def process_onboarding(session: AsyncSession, telegram_id: int, username: str | None, first_name: str | None, ref_id: int | None = None) -> User:
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
    async def handle_successful_payment(session: AsyncSession, payment_id: int) -> bool:
        payment = await get_last_payment(session, payment_id)
        if not payment:
            return False
            
        if payment.status == 'paid':
            return True
            
        payment = await mark_payment_as_paid(session, payment)
        
        tariff = payment.tariff
        user = await get_user_by_telegram_id(session, payment.user_id)
        
        await extend_subscription(session, user, tariff.duration_days)
        
        # Реферальная система
        if user.last_payment_at is None and user.referred_by:
            referrer = await get_user_by_telegram_id(session, user.referred_by)
            if referrer:
                bonus_days = get_settings().REFERRAL_BONUS_DAYS
                await extend_subscription(session, referrer, bonus_days)
                referrer.referral_days += bonus_days
                await update_user(session, referrer, referral_days=referrer.referral_days)
                
        user.last_payment_at = datetime.utcnow()
        await update_user(session, user, last_payment_at=user.last_payment_at)
        
        await session.commit()
        return True
