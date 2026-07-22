from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import HubMessage


async def get_hub_message_ids(session: AsyncSession, chat_id: int) -> list[int]:
    stmt = (
        select(HubMessage.message_id)
        .where(HubMessage.chat_id == chat_id)
        .order_by(HubMessage.created_at.asc())
    )
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]


async def add_hub_message_id(
    session: AsyncSession,
    chat_id: int,
    message_id: int,
) -> None:
    hub_message = HubMessage(chat_id=chat_id, message_id=message_id)
    session.add(hub_message)
    await session.flush()


async def remove_hub_message_ids(
    session: AsyncSession,
    chat_id: int,
    message_ids: list[int],
) -> None:
    if not message_ids:
        return

    stmt = delete(HubMessage).where(
        HubMessage.chat_id == chat_id,
        HubMessage.message_id.in_(message_ids),
    )
    await session.execute(stmt)
    await session.flush()