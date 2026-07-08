from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User
from datetime import datetime, timedelta
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

async def extend_subscription(session: AsyncSession, user: User, days: int) -> User:
    if user.subscription_end is None or user.subscription_end < datetime.utcnow():
        user.subscription_end = datetime.utcnow() + timedelta(days=days)
    else:
        user.subscription_end += timedelta(days=days)
    await session.commit()
    await session.refresh(user)
    return user

async def get_all_users(session: AsyncSession, limit: int = 100, offset: int = 0) -> List[User]:
    stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_user_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id))
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_active_subscriptions_count(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.subscription_end > datetime.utcnow())
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_new_users_count_24h(session: AsyncSession) -> int:
    stmt = select(func.count(User.id)).where(User.created_at > datetime.utcnow() - timedelta(hours=24))
    result = await session.execute(stmt)
    return result.scalar_one()
