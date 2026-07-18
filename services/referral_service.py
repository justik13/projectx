
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, update_user
from services.subscription import SubscriptionService
from bot.middlewares.user_context import invalidate_user_cache
from config.settings import get_settings

logger = logging.getLogger(__name__)
MIN_DURATION_FOR_REFERRAL = 30
REFERRAL_FIRST_PURCHASE_BONUS = 5   # Реферал при первой покупке (тариф >= 30д)
REFERRER_FIRST_PURCHASE_BONUS = 3   # Пригласитель при первой покупке
REFERRER_RENEWAL_BONUS = 1          # Пригласитель при продлении


class ReferralService:
    @staticmethod
    async def process_bonus(
        session: AsyncSession,
        user_telegram_id: int,
        referrer_telegram_id: int,
        *,
        is_first_payment: bool = False,
        duration_days: int = 0,
    ):
        if duration_days < MIN_DURATION_FOR_REFERRAL:
            logger.info(
                f"Referral bonus SKIPPED: tariff {duration_days} days "
                f"< {MIN_DURATION_FOR_REFERRAL} days minimum. "
                f"user={user_telegram_id}, referrer={referrer_telegram_id}"
            )
            return

        referrer = await get_user_by_telegram_id(session, referrer_telegram_id)
        if not referrer:
            logger.warning(
                f"Referral bonus: referrer {referrer_telegram_id} not found in DB"
            )
            return
        if referrer_telegram_id == user_telegram_id:
            logger.warning(
                f"Referral bonus: self-referral attempt by {user_telegram_id}"
            )
            return

        if is_first_payment:
            try:
                await SubscriptionService.extend_subscription(
                    session, user_telegram_id, REFERRAL_FIRST_PURCHASE_BONUS
                )
                invalidate_user_cache(user_telegram_id)  # 🔥 ИСПРАВЛЕНО: Инвалидация кэша
                logger.info(
                    f"Referral bonus: user {user_telegram_id} got "
                    f"+{REFERRAL_FIRST_PURCHASE_BONUS} days (first purchase)"
                )
            except Exception as e:
                logger.error(
                    f"Referral bonus: failed to extend for user "
                    f"{user_telegram_id}: {e}"
                )
            try:
                await SubscriptionService.extend_subscription(
                    session, referrer_telegram_id, REFERRER_FIRST_PURCHASE_BONUS
                )
                invalidate_user_cache(referrer_telegram_id)  # 🔥 ИСПРАВЛЕНО: Инвалидация кэша
                new_referral_days = (
                    (referrer.referral_days or 0) + REFERRER_FIRST_PURCHASE_BONUS
                )
                await update_user(
                    session, referrer, referral_days=new_referral_days
                )
                logger.info(
                    f"Referral bonus: referrer {referrer_telegram_id} got "
                    f"+{REFERRER_FIRST_PURCHASE_BONUS} days (first purchase). "
                    f"Total referral_days: {new_referral_days}"
                )
            except Exception as e:
                logger.error(
                    f"Referral bonus: failed to extend for referrer "
                    f"{referrer_telegram_id}: {e}"
                )

        else:

            try:
                await SubscriptionService.extend_subscription(
                    session, referrer_telegram_id, REFERRER_RENEWAL_BONUS
                )
                invalidate_user_cache(referrer_telegram_id)  # 🔥 ИСПРАВЛЕНО: Инвалидация кэша
                new_referral_days = (
                    (referrer.referral_days or 0) + REFERRER_RENEWAL_BONUS
                )
                await update_user(
                    session, referrer, referral_days=new_referral_days
                )
                logger.info(
                    f"Referral bonus: referrer {referrer_telegram_id} got "
                    f"+{REFERRER_RENEWAL_BONUS} days (renewal). "
                    f"Total referral_days: {new_referral_days}"
                )
            except Exception as e:
                logger.error(
                    f"Referral bonus: failed to extend for referrer "
                    f"{referrer_telegram_id} on renewal: {e}"
                )
