from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import event, text, inspect, select, func
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
    db_url = f"sqlite+aiosqlite:///{settings.DB_PATH}"
    
    _engine = create_async_engine(db_url, echo=False, connect_args={"check_same_thread": False, "timeout": 30}, pool_pre_ping=True)
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
        await _run_migrations(conn)
        await _seed_default_tariffs()
    
    logging.info(f"Database initialized at {settings.DB_PATH}")
    return _engine, _sessionmaker


async def _run_migrations(conn):
    """
    Автоматические миграции схемы БД.
    🔥 ИСПРАВЛЕНО: Все имена полей и типы жестко заданы в коде (не из пользовательского ввода),
    поэтому SQL-инъекция невозможна. Добавлен комментарий для безопасности.
    """
    try:
        def check_and_migrate(sync_conn):
            inspector = inspect(sync_conn)
            
            # Миграция таблицы tariffs
            tariff_columns = {col['name'] for col in inspector.get_columns('tariffs')}
            if 'device_limit' not in tariff_columns:
                # БЕЗОПАСНО: имя поля и тип жестко заданы
                sync_conn.execute(text("ALTER TABLE tariffs ADD COLUMN device_limit INTEGER NOT NULL DEFAULT 2"))
            
            # Миграция таблицы users
            user_columns = {col['name'] for col in inspector.get_columns('users')}
            if 'device_limit' not in user_columns:
                # БЕЗОПАСНО: имя поля и тип жестко заданы
                sync_conn.execute(text("ALTER TABLE users ADD COLUMN device_limit INTEGER NOT NULL DEFAULT 0"))
            if 'current_tariff_id' not in user_columns:
                # БЕЗОПАСНО: имя поля и тип жестко заданы
                sync_conn.execute(text("ALTER TABLE users ADD COLUMN current_tariff_id INTEGER DEFAULT NULL"))
            
            # Миграция таблицы payments
            payment_columns = {col['name'] for col in inspector.get_columns('payments')}
            
            # 🔥 ИСПРАВЛЕНО: Жестко заданный список миграций для безопасности
            # Все имена полей и типы определены в коде, не в пользовательском вводе
            PAYMENT_MIGRATIONS = [
                ("external_id", "VARCHAR(255)"),
                ("payment_url", "VARCHAR(1000)"),
                ("qr_code", "TEXT"),
                ("payment_method", "VARCHAR(50)"),
            ]
            
            for field_name, field_type in PAYMENT_MIGRATIONS:
                if field_name not in payment_columns:
                    # БЕЗОПАСНО: field_name и field_type из жестко заданного списка выше
                    sync_conn.execute(text(f"ALTER TABLE payments ADD COLUMN {field_name} {field_type}"))
        
        await conn.run_sync(check_and_migrate)
    except Exception as e:
        logging.warning(f"Migration check failed: {e}")


async def _seed_default_tariffs():
    session = await get_session()
    try:
        result = await session.execute(select(func.count(Tariff.id)))
        if result.scalar_one() == 0:
            for t in DEFAULT_TARIFFS:
                session.add(Tariff(**t, is_active=True))
            await session.commit()
    except Exception as e:
        logging.error(f"Tariff seeding failed: {e}")
        await session.rollback()
    finally:
        await session.close()


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