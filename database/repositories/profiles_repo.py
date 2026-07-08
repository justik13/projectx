from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import VPNProfile
from typing import Optional, List
from utils.encryption import encrypt_value, decrypt_value
from config.settings import get_settings

async def get_user_profiles(session: AsyncSession, user_id: int) -> List[VPNProfile]:
    stmt = select(VPNProfile).where(VPNProfile.user_id == user_id).order_by(VPNProfile.created_at.desc())
    result = await session.execute(stmt)
    profiles = result.scalars().all()
    
    if profiles:
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        # Расшифровываем конфиденциальные данные для каждого профиля
        for profile in profiles:
            profile.peer_id = decrypt_value(profile.peer_id, key)
            profile.raw_config = decrypt_value(profile.raw_config, key)
    
    return profiles

async def get_profile_by_id(session: AsyncSession, profile_id: int) -> Optional[VPNProfile]:
    stmt = select(VPNProfile).where(VPNProfile.id == profile_id)
    result = await session.execute(stmt)
    profile = result.scalar_one_or_none()
    
    if profile:
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        # Расшифровываем конфиденциальные данные после чтения из БД
        profile.peer_id = decrypt_value(profile.peer_id, key)
        profile.raw_config = decrypt_value(profile.raw_config, key)
    
    return profile

async def create_profile(session: AsyncSession, user_id: int, server_id: int, device_name: str, peer_id: str, raw_config: str) -> VPNProfile:
    settings = get_settings()
    key = settings.DB_ENCRYPTION_KEY
    
    # Шифруем конфиденциальные данные перед сохранением в БД
    encrypted_peer_id = encrypt_value(peer_id, key)
    encrypted_raw_config = encrypt_value(raw_config, key)
    
    profile = VPNProfile(
        user_id=user_id,
        server_id=server_id,
        device_name=device_name,
        peer_id=encrypted_peer_id,
        raw_config=encrypted_raw_config
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
