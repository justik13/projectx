from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import VPNProfile
from typing import Optional, List

async def get_user_profiles(session: AsyncSession, user_id: int) -> List[VPNProfile]:
    stmt = select(VPNProfile).where(VPNProfile.user_id == user_id).order_by(VPNProfile.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_profile_by_id(session: AsyncSession, profile_id: int) -> Optional[VPNProfile]:
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
    await session.commit()
    await session.refresh(profile)
    return profile

async def update_profile(session: AsyncSession, profile: VPNProfile, **kwargs) -> VPNProfile:
    for key, value in kwargs.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    await session.commit()
    await session.refresh(profile)
    return profile

async def delete_profile(session: AsyncSession, profile: VPNProfile) -> None:
    await session.delete(profile)
    await session.commit()

async def get_user_profiles_count(session: AsyncSession, user_id: int) -> int:
    stmt = select(func.count(VPNProfile.id)).where(VPNProfile.user_id == user_id)
    result = await session.execute(stmt)
    return result.scalar_one()

async def get_all_profiles_count(session: AsyncSession) -> int:
    """Посчитать общее количество профилей (устройств)"""
    result = await session.execute(select(func.count()).select_from(VPNProfile))
    return result.scalar() or 0