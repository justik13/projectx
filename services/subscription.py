from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from database.repositories.payments_repo import create_payment, get_payment_by_id
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
        return user.subscription_end > datetime.now(timezone.utc)

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
                
        return await create_user(
            session, 
            telegram_id, 
            username, 
            first_name, 
            referred_by
        )

    @staticmethod
    async def extend_subscription(
        session: AsyncSession, 
        telegram_id: int, 
        days: int
    ) -> Optional[User]:
        """
        Продлить подписку пользователя на указанное количество дней.
        Если подписки нет — создаёт новую от текущей даты.
        Если days >= 36500 — даёт "вечную" подписку (на 100 лет).
        """
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            logging.warning(f"extend_subscription: user {telegram_id} not found")
            return None
        
        now = datetime.now(timezone.utc)
        
        # Если подписка ещё активна — продлеваем от её окончания
        # Если истекла или её нет — продлеваем от текущего момента
        if user.subscription_end and user.subscription_end > now:
            current_end = user.subscription_end
        else:
            current_end = now
        
        # "Вечная" подписка
        if days >= 36500:
            new_end = datetime(2100, 1, 1, tzinfo=timezone.utc)
        else:
            new_end = current_end + timedelta(days=days)
        
        await update_user(session, user, subscription_end=new_end)
        
        logging.info(
            f"Extended subscription for user {telegram_id}: "
            f"+{days} days, new end: {new_end}"
        )
        
        return user

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, 
        payment_id: int
    ) -> bool:
        """
        Обработать успешный платёж:
        1. Пометить платёж как оплаченный
        2. Продлить подписку пользователю
        3. Начислить бонус рефереру (при первой оплате)
        """
        payment = await get_payment_by_id(session, payment_id)
        if not payment:
            logging.error(f"Payment {payment_id} not found")
            return False
            
        if payment.status == 'completed':
            logging.info(f"Payment {payment_id} already completed")
            return True
        
        # Помечаем как оплаченный
        payment.status = 'completed'
        payment.paid_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(payment)
        
        # Получаем связанные данные
        tariff = payment.tariff
        user = payment.user
        
        if not tariff or not user:
            logging.error(f"Payment {payment_id}: missing tariff or user")
            return False
        
        # Продлеваем подписку
        await SubscriptionService.extend_subscription(
            session, 
            user.telegram_id, 
            tariff.duration_days
        )
        
        # Реферальная система: бонус за ПЕРВУЮ оплату
        is_first_payment = user.last_payment_at is None
        
        if is_first_payment and user.referred_by:
            referrer = await get_user_by_telegram_id(session, user.referred_by)
            if referrer:
                bonus_days = get_settings().REFERRAL_BONUS_DAYS
                
                # Продлеваем подписку рефереру
                await SubscriptionService.extend_subscription(
                    session, 
                    referrer.telegram_id, 
                    bonus_days
                )
                
                # Увеличиваем счётчик бонусных дней
                new_referral_days = (referrer.referral_days or 0) + bonus_days
                await update_user(
                    session, 
                    referrer, 
                    referral_days=new_referral_days
                )
                
                logging.info(
                    f"Referral bonus: user {user.telegram_id} first payment, "
                    f"referrer {referrer.telegram_id} got +{bonus_days} days"
                )
        
        # Обновляем дату последнего платежа
        await update_user(
            session, 
            user, 
            last_payment_at=datetime.now(timezone.utc)
        )
        
        await session.commit()
        
        logging.info(
            f"Payment {payment_id} processed successfully: "
            f"user={user.telegram_id}, tariff={tariff.id}, "
            f"days={tariff.duration_days}"
        )
        
        return True
