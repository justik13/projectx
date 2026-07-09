from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Server, VPNProfile
from typing import Optional, List

async def get_all_servers(session: AsyncSession) -> List[Server]:
    stmt = select(Server).order_by(Server.name)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_active_servers(session: AsyncSession) -> List[Server]:
    stmt = select(Server).where(Server.is_active == True).order_by(Server.name)
    result = await session.execute(stmt)
    return result.scalars().all()

async def get_server_by_id(session: AsyncSession, server_id: int) -> Optional[Server]:
    stmt = select(Server).where(Server.id == server_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

async def create_server(session: AsyncSession, name: str, api_url: str, api_key: str, country_flag: str = None, description: str = None, protocol: str = "amneziawg2", max_clients: int = 50) -> Server:
    server = Server(
        name=name,
        api_url=api_url,
        api_key=api_key,
        country_flag=country_flag,
        description=description,
        protocol=protocol,
        max_clients=max_clients
    )
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return server

async def update_server(session: AsyncSession, server: Server, **kwargs) -> Server:
    for key, value in kwargs.items():
        if hasattr(server, key):
            setattr(server, key, value)
    await session.commit()
    await session.refresh(server)
    return server

async def delete_server(session: AsyncSession, server: Server) -> None:
    await session.delete(server)
    await session.commit()

async def get_total_free_ips(session: AsyncSession) -> int:
    """Посчитать общее количество свободных IP на всех серверах"""
    result = await session.execute(
        select(func.sum(Server.max_clients)).where(Server.is_active == True)
    )
    total_capacity = result.scalar() or 0
    
    # Вычитаем количество созданных профилей на активных серверах
    from sqlalchemy import select
    
    active_server_ids = select(Server.id).where(Server.is_active == True).scalar_subquery()
    stmt = select(func.count(VPNProfile.id)).where(VPNProfile.server_id.in_(active_server_ids))
    result = await session.execute(stmt)
    used_profiles = result.scalar() or 0
    
    return max(0, total_capacity - used_profiles)
