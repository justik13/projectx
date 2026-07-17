"""
Сервис реферальных бонусов.

🔥 ИСПРАВЛЕНО (Часть 3): Новая логика начисления реферальных бонусов.

Правила:
- Тариф 7 дней: рефералка ПОЛНОСТЬЮ игнорируется (ничего не начисляется)
- Тариф >= 30 дней, ПЕРВАЯ покупка:
  - Реферал (тот кто купил): +5 дней
  - Пригласитель (тот кто пригласил): +3 дня
- Тариф >= 30 дней, ПРОДЛЕНИЕ:
  - Пригласитель: +1 день
  - Реферал: ничего не получает

Антифрод: бонусы начисляются ТОЛЬКО при успешной оплате,
а не при регистрации по реферальной ссылке.
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, update_user
from services.subscription import SubscriptionService
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Минимальная длительность тарифа для участия в реферальной программе
MIN_DURATION_FOR_REFERRAL = 30

# Бонусы
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
        """
        Начисляет реферальные бонусы при успешной оплате.
        
        Args:
            session: SQLAlchemy async session
            user_telegram_id: Telegram ID покупателя (реферала)
            referrer_telegram_id: Telegram ID пригласившего
            is_first_payment: True если это первая успешная оплата пользователя
            duration_days: Длительность купленного тарифа в днях
            
        Returns:
            None
        """
        # Правило 1: Тариф < 30 дней — рефералка игнорируется
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

        # Не начисляем бонус самому себе (edge case)
        if referrer_telegram_id == user_telegram_id:
            logger.warning(
                f"Referral bonus: self-referral attempt by {user_telegram_id}"
            )
            return

        if is_first_payment:
            # ═══════════════════════════════════════════════════════════
            # ПЕРВАЯ ПОКУПКА (тариф >= 30 дней)
            # Реферал: +5 дней
            # Пригласитель: +3 дня
            # ═══════════════════════════════════════════════════════════

            # Бонус рефералу (покупателю)
            try:
                await SubscriptionService.extend_subscription(
                    session, user_telegram_id, REFERRAL_FIRST_PURCHASE_BONUS
                )
                logger.info(
                    f"Referral bonus: user {user_telegram_id} got "
                    f"+{REFERRAL_FIRST_PURCHASE_BONUS} days (first purchase)"
                )
            except Exception as e:
                logger.error(
                    f"Referral bonus: failed to extend for user "
                    f"{user_telegram_id}: {e}"
                )

            # Бонус пригласителю
            try:
                await SubscriptionService.extend_subscription(
                    session, referrer_telegram_id, REFERRER_FIRST_PURCHASE_BONUS
                )
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
            # ═══════════════════════════════════════════════════════════
            # ПРОДЛЕНИЕ (тариф >= 30 дней)
            # Пригласитель: +1 день
            # Реферал: ничего не получает
            # ═══════════════════════════════════════════════════════════

            try:
                await SubscriptionService.extend_subscription(
                    session, referrer_telegram_id, REFERRER_RENEWAL_BONUS
                )
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
