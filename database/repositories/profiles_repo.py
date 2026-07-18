from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from database.models import VPNProfile
ALLOWED_PROFILE_UPDATE_FIELDS = {
    'device_name', 'last_connected', 'traffic_down', 'traffic_up',
    'last_ip', 'is_active', 'sync_fail_count'
}

async def get_user_profiles(session: AsyncSession, user_id: int) -> list[VPNProfile]:
    stmt = select(VPNProfile).where(VPNProfile.user_id == user_id).options(selectinload(VPNProfile.server)).order_by(VPNProfile.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_profile_by_id(session: AsyncSession, profile_id: int) -> VPNProfile | None:
    stmt = select(VPNProfile).where(VPNProfile.id == profile_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def create_profile(session: AsyncSession, user_id: int, server_id: int, device_name: str, peer_id: str, raw_config: str) -> VPNProfile:
    profile = VPNProfile(
        user_id=user_id,
        server_id=server_id,
        device_name=device_name,
        peer_id=peer_id,
        raw_config=raw_config
    )
    session.add(profile)
    await session.flush()
    await session.refresh(profile)
    return profile

async def update_profile(session: AsyncSession, profile: VPNProfile, **kwargs) -> VPNProfile:
    for key, value in kwargs.items():
        if key in ALLOWED_PROFILE_UPDATE_FIELDS:
            setattr(profile, key, value)
    await session.flush()
    await session.refresh(profile)
    return profile

async def delete_profile(session: AsyncSession, profile: VPNProfile) -> None:
    await session.delete(profile)
    await session.flush()

async def get_user_profiles_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(VPNProfile.id)).where(VPNProfile.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_all_profiles_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(VPNProfile))
    return result.scalar() or 0