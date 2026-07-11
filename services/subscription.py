from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from database.models import User
from datetime import datetime, timedelta, timezone
from typing import Optional
from bot.constants import PERMANENT_SUBSCRIPTION_DAYS, PERMANENT_END_DATE
import logging


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
        return await create_user(session, telegram_id, username, first_name, referred_by)

    @staticmethod
    async def extend_subscription(session: AsyncSession, telegram_id: int, days: int) -> Optional[User]:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        current_end = user.subscription_end if (user.subscription_end and user.subscription_end > now) else now
        new_end = PERMANENT_END_DATE if days >= PERMANENT_SUBSCRIPTION_DAYS else current_end + timedelta(days=days)
        user.subscription_end = new_end
        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False
        await session.commit()
        return user

    @staticmethod
    async def get_expires_timestamp(user: User) -> Optional[int]:
        if not user.subscription_end or user.subscription_end.year >= 2100:
            return None
        return int(user.subscription_end.replace(tzinfo=timezone.utc).timestamp())