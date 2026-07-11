from aiogram import BaseMiddleware
from database.connection import get_session


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with get_session() as session:
            data['session'] = session
            return await handler(event, data)