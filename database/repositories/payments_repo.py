from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Payment
from datetime import datetime, timezone
from typing import Optional, List

async def create_payment(session: AsyncSession, user_id: int, tariff_id: int, amount: int, currency: str) -> Payment:
    payment = Payment(
        user_id=user_id,
        tariff_id=tariff_id,
        amount=amount,
        currency=currency
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment

async def mark_payment_as_paid(session: AsyncSession, payment: Payment) -> Payment:
    payment.status = 'paid'
    payment.paid_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(payment)
    return payment

async def get_user_payments(session: AsyncSession, user_id: int) -> List[Payment]:
    stmt = select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_last_payment(session: AsyncSession, user_id: int) -> Optional[Payment]:
    stmt = select(Payment).where(Payment.user_id == user_id).order_by(Payment.created_at.desc()).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def get_payment_by_id(session: AsyncSession, payment_id: int) -> Optional[Payment]:
    """Получить платёж по ID с загрузкой связей"""
    from sqlalchemy.orm import selectinload
    
    result = await session.execute(
        select(Payment)
        .options(
            selectinload(Payment.user),
            selectinload(Payment.tariff)
        )
        .where(Payment.id == payment_id)
    )
    return result.scalar_one_or_none()
