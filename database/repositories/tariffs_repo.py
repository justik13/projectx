from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Tariff
from typing import Optional, List


async def get_active_tariffs(session: AsyncSession) -> List[Tariff]:
    stmt = select(Tariff).where(Tariff.is_active == True).order_by(
        Tariff.device_limit, Tariff.sort_order, Tariff.duration_days
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_tariff_by_id(session: AsyncSession, tariff_id: int) -> Optional[Tariff]:
    stmt = select(Tariff).where(Tariff.id == tariff_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_tariff(
    session: AsyncSession,
    duration_days: int,
    device_limit: int,
    price_rub: int,
    price_stars: int,
    sort_order: int = 0,
) -> Tariff:
    tariff = Tariff(
        duration_days=duration_days,
        device_limit=device_limit,
        price_rub=price_rub,
        price_stars=price_stars,
        sort_order=sort_order,
    )
    session.add(tariff)
    await session.flush()
    await session.refresh(tariff)
    return tariff


async def update_tariff(session: AsyncSession, tariff: Tariff, **kwargs) -> Tariff:
    for key, value in kwargs.items():
        if hasattr(tariff, key):
            setattr(tariff, key, value)
    await session.flush()
    await session.refresh(tariff)
    return tariff


async def delete_tariff(session: AsyncSession, tariff: Tariff) -> None:
    await session.delete(tariff)
    await session.flush()


async def get_tariff_count(session: AsyncSession) -> int:
    stmt = select(func.count(Tariff.id))
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_tariffs_paginated(session: AsyncSession, page: int = 1, per_page: int = 10) -> list[Tariff]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Tariff).order_by(
            Tariff.device_limit, Tariff.sort_order, Tariff.duration_days
        ).offset(offset).limit(per_page)
    )
    return result.scalars().all()