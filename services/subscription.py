from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from database.models import User
from datetime import datetime, timedelta, timezone
from typing import Optional
from bot.constants import PERMANENT_SUBSCRIPTION_DAYS, PERMANENT_END_DATE
import logging

logger = logging.getLogger(__name__)


class SubscriptionService:
    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user or user.is_banned or not user.subscription_end:
            return False
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return user.subscription_end > now

    @staticmethod
    async def process_onboarding(
        session: AsyncSession, telegram_id: int,
        username: str | None, first_name: str | None,
        ref_id: int | None = None
    ) -> User:
        user = await get_user_by_telegram_id(session, telegram_id)
        if user:
            return user

        referred_by = None
        if ref_id is not None and ref_id != telegram_id:
            ref_user = await get_user_by_telegram_id(session, ref_id)
            if ref_user:
                referred_by = ref_id
                logger.info(f"New user {telegram_id} referred by {ref_id}")

        return await create_user(session, telegram_id, username, first_name, referred_by)

    @staticmethod
    async def extend_subscription(
        session: AsyncSession, telegram_id: int, days: int,
        new_device_limit: Optional[int] = None,
        new_tariff_id: Optional[int] = None,
    ) -> Optional[User]:
        """
        Продлевает подписку пользователя.

        🔥 ИСПРАВЛЕНО: Сброс daily device creation счётчика при апгрейде тарифа.
        Если new_device_limit > текущего user.device_limit — сбрасываем
        device_creations_today в 0 и last_creation_date в None.
        Это даёт пользователю "чистый лист" после апгрейда.

        Args:
            session: SQLAlchemy async session
            telegram_id: Telegram ID пользователя
            days: Количество дней продления
            new_device_limit: Новый лимит устройств (None = не менять)
            new_tariff_id: ID нового тарифа (None = не менять)

        Returns:
            User если успешно, None если пользователь не найден
        """
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return None

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        current_end = user.subscription_end if (
            user.subscription_end and user.subscription_end > now
        ) else now

        new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else current_end + timedelta(days=days)

        user.subscription_end = new_end
        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False

        # 🔥 ИСПРАВЛЕНО: Сброс daily счётчика при АПГРЕЙДЕ тарифа
        # (Вариант A из согласования)
        if new_device_limit is not None:
            old_device_limit = user.device_limit
            user.device_limit = new_device_limit

            # Сбрасываем daily счётчик ТОЛЬКО при апгрейде (новое > старое)
            # При даунгрейде или продлении того же тарифа — не сбрасываем
            if new_device_limit > old_device_limit:
                user.device_creations_today = 0
                user.last_creation_date = None
                logger.info(
                    f"extend_subscription: user {telegram_id} upgraded from "
                    f"{old_device_limit} to {new_device_limit} devices. "
                    f"Daily creations counter reset to 0."
                )

        if new_tariff_id is not None:
            user.current_tariff_id = new_tariff_id

        await session.flush()
        return user

    @staticmethod
    async def get_expires_timestamp(user: User) -> Optional[int]:
        """
        Возвращает Unix timestamp для expiresAt или None для бессрочного доступа.

        🔥 ИСПРАВЛЕНО #19: Логирование когда expiresAt=null отправляется в API.
        Это нормально для «навсегда» подписок, но полезно для отладки.
        """
        if not user.subscription_end or user.subscription_end.year >= 2100:
            # 🔥 ИСПРАВЛЕНО #19: Явное логирование null expiresAt
            logger.info(
                f"get_expires_timestamp: user {user.telegram_id} has permanent subscription, "
                f"sending expiresAt=null to API"
            )
            return None
        expires_ts = int(user.subscription_end.replace(tzinfo=timezone.utc).timestamp())
        return expires_ts