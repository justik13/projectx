from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from config.settings import get_settings

async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> Optional[User]:
    stmt = select(User).where(User.telegram_id == telegram_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def create_user(session: AsyncSession, telegram_id: int, username: str = None, first_name: str = None, referred_by: int = None) -> User:
    settings = get_settings()
    user = User(
        telegram_id=telegram_id,
        username=username,
        first_name=first_name,
        referred_by=referred_by,
        device_limit=settings.DEFAULT_DEVICE_LIMIT
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user

async def update_user(session: AsyncSession, user: User, **kwargs) -> User:
    for key, value in kwargs.items():
        if hasattr(user, key):
            setattr(user, key, value)
    await session.commit()
    await session.refresh(user)
    return user

async def extend_subscription(
    session: AsyncSession, 
    user: User, 
    days: int
) -> User:
    """
    Продлить подписку пользователя (низкоуровневая функция).
    Для высокоуровневой работы используйте SubscriptionService.extend_subscription
    """
    from datetime import datetime, timedelta
    
    now = datetime.now(timezone.utc)
    if user.subscription_end and user.subscription_end > now:
        current_end = user.subscription_end
    else:
        current_end = now
    
    if days >= 36500:
        new_end = datetime(2100, 1, 1, tzinfo=timezone.utc)
    else:
        new_end = current_end + timedelta(days=days)
    
    return await update_user(session, user, subscription_end=new_end)

async def get_all_users(session: AsyncSession, limit: int | None = None, offset: int = 0) -> List[User]:
    stmt = select(User).order_by(User.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    if offset > 0:
        stmt = stmt.offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_user_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id))
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_active_subscriptions_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.subscription_end > datetime.now(timezone.utc))
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_new_users_count_24h(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.created_at > datetime.now(timezone.utc) - timedelta(hours=24))
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_users_paginated(session: AsyncSession, page: int = 1, per_page: int = 10) -> list[User]:
    """Получить пользователей с пагинацией"""
    offset = (page - 1) * per_page
    result = await session.execute(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(per_page)
    )
    return result.scalars().all()


async def get_active_users(session: AsyncSession) -> list[User]:
    """Получить пользователей с активной подпиской"""
    result = await session.execute(
        select(User).where(User.subscription_end > datetime.now(timezone.utc))
    )
    return result.scalars().all()


async def get_user_referrals(session: AsyncSession, telegram_id: int) -> list[User]:
    """Получить список рефералов пользователя"""
    stmt = select(User).where(User.referred_by == telegram_id).order_by(User.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()
