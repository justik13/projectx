from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Server
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
