import logging
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id
from services.subscription import SubscriptionService
from config.settings import get_settings

logger = logging.getLogger(__name__)


class ReferralService:
    @staticmethod
    async def process_bonus(session: AsyncSession, user_telegram_id: int, referrer_telegram_id: int):
        referrer = await get_user_by_telegram_id(session, referrer_telegram_id)
        if not referrer:
            return
        bonus_days = get_settings().REFERRAL_BONUS_DAYS
        await SubscriptionService.extend_subscription(session, referrer_telegram_id, bonus_days)
        referrer.referral_days = (referrer.referral_days or 0) + bonus_days
        logger.info(f"Referral bonus: user {user_telegram_id} first payment, referrer {referrer_telegram_id} got +{bonus_days} days")