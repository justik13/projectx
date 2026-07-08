from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Tariff
from typing import Optional, List

async def get_all_tariffs(session: AsyncSession) -> List[Tariff]:
    stmt = select(Tariff).order_by(Tariff.sort_order, Tariff.duration_days)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_active_tariffs(session: AsyncSession) -> List[Tariff]:
    stmt = select(Tariff).where(Tariff.is_active == True).order_by(Tariff.sort_order, Tariff.duration_days)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_tariff_by_id(session: AsyncSession, tariff_id: int) -> Optional[Tariff]:
    stmt = select(Tariff).where(Tariff.id == tariff_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def create_tariff(session: AsyncSession, duration_days: int, price_rub: int, price_stars: int, sort_order: int = 0) -> Tariff:
    tariff = Tariff(
        duration_days=duration_days,
        price_rub=price_rub,
        price_stars=price_stars,
        sort_order=sort_order
    )
    session.add(tariff)
    await session.commit()
    await session.refresh(tariff)
    return tariff

async def update_tariff(session: AsyncSession, tariff: Tariff, **kwargs) -> Tariff:
    for key, value in kwargs.items():
        if hasattr(tariff, key):
            setattr(tariff, key, value)
    await session.commit()
    await session.refresh(tariff)
    return tariff

async def delete_tariff(session: AsyncSession, tariff: Tariff) -> None:
    await session.delete(tariff)
    await session.commit()

async def get_all_tariffs(session: AsyncSession) -> list[Tariff]:
    """Получить все тарифы"""
    result = await session.execute(select(Tariff))
    return result.scalars().all()


async def create_tariff(
    session: AsyncSession,
    duration_days: int,
    price_rub: int,
    price_stars: int
) -> Tariff:
    """Создать новый тариф"""
    tariff = Tariff(
        duration_days=duration_days,
        price_rub=price_rub,
        price_stars=price_stars,
        is_active=True
    )
    session.add(tariff)
    await session.commit()
    await session.refresh(tariff)
    return tariff


async def update_tariff(session: AsyncSession, tariff: Tariff, **kwargs) -> Tariff:
    """Обновить тариф"""
    for key, value in kwargs.items():
        setattr(tariff, key, value)
    await session.commit()
    await session.refresh(tariff)
    return tariff


async def delete_tariff(session: AsyncSession, tariff_id: int):
    """Удалить тариф"""
    tariff = await get_tariff_by_id(session, tariff_id)
    if tariff:
        await session.delete(tariff)
        await session.commit()