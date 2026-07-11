from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import AuditLog
from typing import List, Optional


async def create_audit_log(
    session: AsyncSession,
    admin_id: int,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    details: Optional[str] = None
) -> AuditLog:
    log = AuditLog(
        admin_id=admin_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log


async def get_recent_audit_logs(session: AsyncSession, limit: int = 10) -> List[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def clear_audit_logs(session: AsyncSession, older_than_days: int = 30) -> int:
    from datetime import datetime, timedelta, timezone
    threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=older_than_days)
    from sqlalchemy import delete
    stmt = delete(AuditLog).where(AuditLog.created_at < threshold)
    result = await session.execute(stmt)
    await session.commit()
    return result.rowcount
