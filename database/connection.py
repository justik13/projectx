from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func
from database.models import Base, Tariff
from config.settings import get_settings
from contextlib import asynccontextmanager
import logging

_engine = None
_sessionmaker = None

DEFAULT_TARIFFS = [
    {"duration_days": 7, "device_limit": 2, "price_rub": 35, "price_stars": 35, "sort_order": 10},
    {"duration_days": 30, "device_limit": 2, "price_rub": 90, "price_stars": 90, "sort_order": 11},
    {"duration_days": 90, "device_limit": 2, "price_rub": 240, "price_stars": 240, "sort_order": 12},
    {"duration_days": 30, "device_limit": 5, "price_rub": 180, "price_stars": 180, "sort_order": 20},
    {"duration_days": 90, "device_limit": 5, "price_rub": 480, "price_stars": 480, "sort_order": 21},
    {"duration_days": 30, "device_limit": 10, "price_rub": 320, "price_stars": 320, "sort_order": 30},
    {"duration_days": 90, "device_limit": 10, "price_rub": 850, "price_stars": 850, "sort_order": 31},
]


async def init_db():
    global _engine, _sessionmaker
    settings = get_settings()
    
    # Используем asyncpg для PostgreSQL
    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
        pool_pre_ping=True, # Проверка соединения перед использованием (важно для PG)
    )
    
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    
    # Создаем все таблицы на основе моделей (без миграций, так как старых данных нет)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _seed_default_tariffs(conn)
    
    logging.info(f"PostgreSQL database initialized at {settings.DATABASE_URL}")
    return _engine, _sessionmaker


async def _seed_default_tariffs(conn):
    """Сидирование тарифов по умолчанию"""
    result = await conn.execute(select(func.count(Tariff.id)))
    if result.scalar_one() == 0:
        for t in DEFAULT_TARIFFS:
            await conn.execute(
                Tariff.__table__.insert().values(**t, is_active=True)
            )
        logging.info("Default tariffs seeded successfully.")


async def get_session() -> AsyncSession:
    global _sessionmaker
    if _sessionmaker is None:
        await init_db()
    return _sessionmaker()


@asynccontextmanager
async def session_scope():
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