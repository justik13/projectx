from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from database.models import User
from datetime import datetime, timedelta, timezone
from typing import Optional, List, TypedDict


class UserUpdateFields(TypedDict, total=False):
    username: str | None
    first_name: str | None
    subscription_end: datetime | None
    device_limit: int
    current_tariff_id: int | None
    referred_by: int | None
    referral_days: int
    last_payment_at: datetime | None
    is_banned: bool
    is_admin: bool
    is_bot_blocked: bool
    notified_3d: bool
    notified_1d: bool
    notified_2h: bool
    tos_accepted: bool


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_id: int
) -> Optional[User]:
    stmt = select(User).where(
        User.telegram_id == telegram_id,
        User.is_deleted == False,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession, telegram_id: int,
    username: str = None, first_name: str = None,
    referred_by: int = None
) -> User:
    user = User(
        telegram_id=telegram_id, username=username,
        first_name=first_name, referred_by=referred_by,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


async def update_user(
    session: AsyncSession, user: User, **kwargs: UserUpdateFields
) -> User:
    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)
    await session.flush()
    await session.refresh(user)
    return user


async def extend_subscription(
    session: AsyncSession, user: User, days: int
) -> User:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if user.subscription_end and user.subscription_end > now:
        current_end = user.subscription_end
    else:
        current_end = now
    if days >= 36500:
        from bot.constants import PERMANENT_END_DATE
        new_end = PERMANENT_END_DATE
    else:
        new_end = current_end + timedelta(days=days)
    return await update_user(session, user, subscription_end=new_end)


async def get_all_users(
    session: AsyncSession, limit: int | None = None, offset: int = 0
) -> List[User]:
    stmt = select(User).where(User.is_deleted == False).order_by(User.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset > 0:
        stmt = stmt.offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.is_deleted == False)
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_active_subscriptions_count(session: AsyncSession) -> int:
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(func.count(User.id)).where(
        User.subscription_end > now_naive,
        User.is_deleted == False,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_new_users_count_24h(session: AsyncSession) -> int:
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    stmt = select(func.count(User.id)).where(
        User.created_at > now_naive - timedelta(hours=24),
        User.is_deleted == False,
    )
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_users_paginated(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> list[User]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(User)
        .where(User.is_deleted == False)
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    return result.scalars().all()


async def get_users_paginated_with_profiles(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> list[User]:
    offset = (page - 1) * per_page
    stmt = (
        select(User)
        .where(User.is_deleted == False)
        .options(selectinload(User.profiles))
        .order_by(User.created_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().all()


async def get_active_users(session: AsyncSession) -> list[User]:
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await session.execute(
        select(User).where(
            User.subscription_end > now_naive,
            User.is_banned == False,
            User.is_bot_blocked == False,
            User.is_deleted == False,
        )
    )
    return result.scalars().all()


async def get_user_referrals(
    session: AsyncSession, telegram_id: int
) -> list[User]:
    stmt = (
        select(User)
        .where(
            User.referred_by == telegram_id,
            User.is_deleted == False,
        )
        .order_by(User.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_user_with_referrals(
    session: AsyncSession, telegram_id: int
) -> tuple[Optional[User], list[User]]:
    """🔥 ИСПРАВЛЕНО: Возвращаем кортеж вместо динамического атрибута"""
    stmt = (
        select(User)
        .options(selectinload(User.profiles))
        .where(
            User.telegram_id == telegram_id,
            User.is_deleted == False,
        )
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    
    referrals = []
    if user:
        referrals = await get_user_referrals(session, telegram_id)
    
    return user, referrals


async def mark_user_bot_blocked(
    session: AsyncSession, telegram_id: int
) -> None:
    await session.execute(
        update(User).where(User.telegram_id == telegram_id).values(is_bot_blocked=True)
    )
    await session.flush()


async def count_users_with_tariff(
    session: AsyncSession, tariff_id: int
) -> int:
    stmt = select(func.count(User.id)).where(
        User.current_tariff_id == tariff_id,
        User.is_deleted == False,
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0