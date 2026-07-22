import logging
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import get_settings
from database.models import MaintenanceMode, Tariff

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

    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=30,
        max_overflow=20,
        pool_timeout=30,
        pool_pre_ping=True,
    )

    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

    async with _engine.begin() as conn:
        await _seed_default_tariffs(conn)
        await _seed_maintenance_mode(conn)

    logging.info("PostgreSQL database initialized at %s", settings.DATABASE_URL)

    return _engine, _sessionmaker


async def _seed_default_tariffs(conn):
    result = await conn.execute(select(func.count(Tariff.id)))

    if result.scalar_one() == 0:
        for tariff in DEFAULT_TARIFFS:
            await conn.execute(
                Tariff.__table__.insert().values(**tariff, is_active=True)
            )

        logging.info("Default tariffs seeded successfully.")


async def _seed_maintenance_mode(conn):
    result = await conn.execute(select(func.count(MaintenanceMode.id)))

    if result.scalar_one() == 0:
        await conn.execute(
            MaintenanceMode.__table__.insert().values(
                id=1,
                is_enabled=False,
                message=(
                    "⚠️ Ведутся технические работы. "
                    "Некоторые действия временно недоступны. "
                    "Попробуйте позже."
                ),
            )
        )

        logging.info("Maintenance mode singleton seeded.")


async def get_session() -> AsyncSession:
    global _sessionmaker

    if _sessionmaker is None:
        await init_db()

    return _sessionmaker()


async def _run_post_commit_tasks(session: AsyncSession) -> None:
    tasks: list[Callable[[], Awaitable[None]]] = session.info.pop(
        "post_commit_tasks", []
    )

    if not tasks:
        return

    for task in tasks:
        try:
            await task()
        except Exception as e:
            logging.error("Post-commit task failed: %s", e, exc_info=True)


def queue_post_commit_task(
    session: AsyncSession,
    task: Callable[[], Awaitable[None]],
) -> None:
    if "post_commit_tasks" not in session.info:
        session.info["post_commit_tasks"] = []

    session.info["post_commit_tasks"].append(task)


@asynccontextmanager
async def session_scope():
    session = await get_session()

    try:
        yield session
        await session.commit()
        await _run_post_commit_tasks(session)
    except Exception:
        await session.rollback()
        session.info.pop("post_commit_tasks", None)
        raise
    finally:
        await session.close()


async def close_db():
    global _engine

    if _engine:
        await _engine.dispose()