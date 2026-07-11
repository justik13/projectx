from aiogram import BaseMiddleware
from database.connection import session_scope


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with session_scope() as session:
            data['session'] = session
            return await handler(event, data)