# database/connection.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text, event
from database.models import Base
from config.settings import get_settings
from contextlib import asynccontextmanager
import logging

_engine = None
_sessionmaker = None

async def init_db():
    global _engine, _sessionmaker
    settings = get_settings()
    db_url = f"sqlite+aiosqlite:///{settings.DB_PATH}"
    
    _engine = create_async_engine(
        db_url,
        echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    # 🔥 FIX P0: Принудительно включаем проверку внешних ключей для SQLite
    @event.listens_for(_engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    logging.info(f"Database initialized at {settings.DB_PATH} (WAL + FK enabled)")
    return _engine, _sessionmaker

async def get_session() -> AsyncSession:
    global _sessionmaker
    if _sessionmaker is None:
        await init_db()
    return _sessionmaker()

@asynccontextmanager
async def get_session_ctx():
    """Контекстный менеджер для безопасной работы с сессией БД"""
    session = await get_session()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

async def close_db():
    global _engine
    if _engine:
        await _engine.dispose()
        logging.info("Database connection closed")
