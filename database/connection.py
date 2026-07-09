from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from database.models import Base
from config.settings import get_settings
import logging

_engine = None
_sessionmaker = None

async def init_db():
    global _engine, _sessionmaker
    
    settings = get_settings()
    db_url = f"sqlite+aiosqlite:///{settings.DB_PATH}"
    
    _engine = create_async_engine(db_url, echo=False, connect_args={"check_same_thread": False, "timeout": 30})
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logging.info(f"Database initialized at {settings.DB_PATH}")
    return _engine, _sessionmaker

async def get_session() -> AsyncSession:
    global _sessionmaker
    
    if _sessionmaker is None:
        await init_db()
    
    return _sessionmaker()

async def close_db():
    global _engine
    
    if _engine:
        await _engine.dispose()
        logging.info("Database connection closed")
