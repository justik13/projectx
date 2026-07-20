from decimal import Decimal
from typing import Optional, List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Payment


async def create_payment(
    session: AsyncSession,
    user_id: int,
    tariff_id: int,
    amount: Decimal,
    currency: str,
) -> Payment:
    payment = Payment(
        user_id=user_id,
        tariff_id=tariff_id,
        amount=amount,
        currency=currency,
    )
    session.add(payment)
    await session.flush()
    await session.refresh(payment)
    return payment


async def get_user_payments(session: AsyncSession, user_id: int) -> List[Payment]:
    stmt = (
        select(Payment)
        .where(Payment.user_id == user_id)
        .order_by(Payment.created_at.desc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_payment_by_id(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
            selectinload(Payment.tariff),
        )
        .where(Payment.id == payment_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_payment_by_id_for_update(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = (
        select(Payment)
        .options(
            selectinload(Payment.user),
            selectinload(Payment.tariff),
        )
        .where(Payment.id == payment_id)
        .with_for_update()
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_payment_by_id_simple(
    session: AsyncSession, payment_id: int
) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.id == payment_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_payment_as_cancelled(
    session: AsyncSession, payment_id: int
) -> bool:
    result = await session.execute(
        update(Payment)
        .where(Payment.id == payment_id, Payment.status == "pending")
        .values(status="cancelled")
    )
    await session.flush()
    return result.rowcount > 0