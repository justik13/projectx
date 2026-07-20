from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MaintenanceMode


async def get_maintenance_mode(session: AsyncSession) -> MaintenanceMode | None:
    stmt = select(MaintenanceMode).where(MaintenanceMode.id == 1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def is_maintenance_enabled(session: AsyncSession) -> bool:
    maintenance = await get_maintenance_mode(session)
    if maintenance is None:
        return False
    return bool(maintenance.is_enabled)


async def set_maintenance_mode(
    session: AsyncSession,
    *,
    is_enabled: bool,
    message: str | None = None,
    updated_by: int | None = None,
) -> MaintenanceMode:
    maintenance = await get_maintenance_mode(session)

    if maintenance is None:
        maintenance = MaintenanceMode(
            id=1,
            is_enabled=is_enabled,
            message=message,
            updated_by=updated_by,
        )
        session.add(maintenance)
    else:
        maintenance.is_enabled = is_enabled
        if message is not None:
            maintenance.message = message
        maintenance.updated_by = updated_by

    await session.flush()
    await session.refresh(maintenance)
    return maintenance