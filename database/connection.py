from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, text, inspect
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
        db_url, echo=False,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True
    )
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

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
        # === АВТОМИГРАЦИЯ: добавляем device_limit в tariffs если его нет ===
        await _run_migrations(conn)

    logging.info(f"Database initialized at {settings.DB_PATH}")
    return _engine, _sessionmaker


async def _run_migrations(conn):
    """Автоматические миграции для существующих БД."""
    try:
        def check_and_migrate(sync_conn):
            inspector = inspect(sync_conn)
            
            # Миграция 1: device_limit в tariffs
            tariff_columns = {col['name'] for col in inspector.get_columns('tariffs')}
            if 'device_limit' not in tariff_columns:
                logging.info("🔄 Migration: adding device_limit to tariffs...")
                sync_conn.execute(text("ALTER TABLE tariffs ADD COLUMN device_limit INTEGER NOT NULL DEFAULT 2"))
                logging.info("✅ Migration: device_limit added to tariffs")
            
            # Миграция 2: device_limit в users (на случай если БД старая)
            user_columns = {col['name'] for col in inspector.get_columns('users')}
            if 'device_limit' not in user_columns:
                logging.info("🔄 Migration: adding device_limit to users...")
                sync_conn.execute(text("ALTER TABLE users ADD COLUMN device_limit INTEGER NOT NULL DEFAULT 2"))
                logging.info("✅ Migration: device_limit added to users")

        await conn.run_sync(check_and_migrate)
    except Exception as e:
        logging.warning(f"Migration check failed (safe to ignore on fresh DB): {e}")


async def get_session() -> AsyncSession:
    global _sessionmaker
    if _sessionmaker is None:
        await init_db()
    return _sessionmaker()


@asynccontextmanager
async def session_scope():
    """Контекстный менеджер для автоматического управления сессией БД"""
    session = await get_session()
    try:
        yield session
        await session.commit()
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