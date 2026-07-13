import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import base64
import zlib
import struct
import json
from database.models import Base, User, Tariff, Server, VPNProfile
from cryptography.fernet import Fernet

@pytest_asyncio.fixture(scope="function")
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    session = AsyncSessionLocal()
    try:
        yield session
    finally:
        await session.close()

@pytest_asyncio.fixture(scope="function")
async def db_user(async_session):
    user = User(
        telegram_id=123456789,
        username="testuser",
        first_name="Test",
        device_limit=2,
        subscription_end=datetime.now() + timedelta(days=30)
    )
    async with async_session.begin():
        async_session.add(user)
        await async_session.commit()
    return user

@pytest_asyncio.fixture(scope="function")
async def db_tariff(async_session):
    tariff = Tariff(
        duration_days=30,
        device_limit=2,
        price_rub=100,
        price_stars=100,
        is_active=True
    )
    async with async_session.begin():
        async_session.add(tariff)
        await async_session.commit()
    return tariff

@pytest_asyncio.fixture(scope="function")
async def db_server(async_session):
    server = Server(
        name="Test Server",
        api_url="http://localhost:4001",
        api_key="test-api-key-12345",
        protocol="amneziawg2",
        max_clients=50,
        is_active=True
    )
    async with async_session.begin():
        async_session.add(server)
        await async_session.commit()
    return server

@pytest_asyncio.fixture(scope="function", autouse=False)
def mock_amnezia_client(mocker):
    amnezia_client_mock = mocker.patch("services.amnezia_client.AmneziaClient")
    instance = amnezia_client_mock.return_value
    instance.create_user.return_value = {"id": "test-peer-id", "config": "vpn://test-uri"}
    instance.delete_user.return_value = True
    instance.update_client.return_value = True
    instance.healthcheck.return_value = True
    instance.get_server_info.return_value = {"protocols": ["amneziawg2"], "maxPeers": 250}
    return amnezia_client_mock

@pytest.fixture(scope="function")
def sample_vpn_uri():
    data = {
        "protocol": "amneziawg2",
        "server": "localhost",
        "port": 4001,
        "peer_id": "test-peer-id"
    }
    json_data = json.dumps(data)
    compressed_data = zlib.compress(json_data.encode())
    encoded_data = base64.b64encode(compressed_data).decode()
    return f"vpn://{encoded_data}"

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("ADMIN_IDS", "123456789")
    monkeypatch.setenv("DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("SUPPORT_USERNAME", "test_support")

@pytest.mark.asyncio
