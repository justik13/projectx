from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Server, VPNProfile
from services.slots_cache import get_real_peer_count
from typing import Optional, List, TypedDict


class ServerUpdateFields(TypedDict, total=False):
    name: str
    country_flag: str | None
    description: str | None
    api_url: str
    protocol: str
    max_clients: int
    is_active: bool


PROTECTED_SERVER_FIELDS = {"id", "api_key", "created_at"}


async def get_active_servers(session: AsyncSession) -> List[Server]:
    stmt = select(Server).where(Server.is_active == True).order_by(Server.name)
    result = await session.execute(stmt)
    return result.scalars().all()


async def get_available_servers(session: AsyncSession) -> List[Server]:
    """Возвращает активные серверы со свободными слотами.

    Один запрос с LEFT JOIN вместо двух отдельных.
    """
    profile_counts = (
        select(
            VPNProfile.server_id,
            func.count(VPNProfile.id).label("profile_count"),
        )
        .group_by(VPNProfile.server_id)
        .subquery()
    )

    stmt = (
        select(Server)
        .outerjoin(profile_counts, Server.id == profile_counts.c.server_id)
        .where(Server.is_active == True)
        .where(
            func.coalesce(profile_counts.c.profile_count, 0)
            < Server.max_clients
        )
        .order_by(Server.name)
    )

    result = await session.execute(stmt)
    return result.scalars().all()


async def get_server_by_id(
    session: AsyncSession, server_id: int
) -> Optional[Server]:
    stmt = select(Server).where(Server.id == server_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_server(
    session: AsyncSession, name: str, api_url: str, api_key: str,
    country_flag: str = None, description: str = None,
    protocol: str = "amneziawg2", max_clients: int = 50
) -> Server:
    server = Server(
        name=name, api_url=api_url, api_key=api_key,
        country_flag=country_flag, description=description,
        protocol=protocol, max_clients=max_clients
    )
    session.add(server)
    await session.flush()
    await session.refresh(server)
    return server


async def update_server(
    session: AsyncSession, server: Server, **kwargs: ServerUpdateFields
) -> Server:
    for key, value in kwargs.items():
        if key in PROTECTED_SERVER_FIELDS:
            continue
        if hasattr(server, key):
            setattr(server, key, value)
    await session.flush()
    await session.refresh(server)
    return server


async def delete_server(session: AsyncSession, server: Server) -> None:
    await session.delete(server)
    await session.flush()


async def get_total_free_ips(session: AsyncSession) -> int:
    active_servers = await get_active_servers(session)
    if not active_servers:
        return 0

    total_free = 0
    for server in active_servers:
        real_count = await get_real_peer_count(server, force_refresh=False)
        if real_count == -1:
            stmt = select(func.count(VPNProfile.id)).where(
                VPNProfile.server_id == server.id
            )
            result = await session.execute(stmt)
            db_count = result.scalar_one() or 0
            real_count = db_count

        free_slots = server.max_clients - real_count
        total_free += max(0, free_slots)

    return total_free


async def get_server_count(session: AsyncSession) -> int:
    stmt = select(func.count(Server.id))
    result = await session.execute(stmt)
    return result.scalar_one()


async def get_servers_paginated(
    session: AsyncSession, page: int = 1, per_page: int = 10
) -> list[Server]:
    offset = (page - 1) * per_page
    result = await session.execute(
        select(Server).order_by(Server.name).offset(offset).limit(per_page)
    )
    return result.scalars().all()


async def get_server_by_api_url(
    session: AsyncSession, api_url: str
) -> Optional[Server]:
    stmt = select(Server).where(Server.api_url == api_url)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def delete_profiles_by_server_id(
    session: AsyncSession, server_id: int
) -> int:
    from sqlalchemy import delete as sql_delete
    stmt = sql_delete(VPNProfile).where(VPNProfile.server_id == server_id)
    result = await session.execute(stmt)
    await session.flush()
    return result.rowcount