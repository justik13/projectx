"""
Общие фикстуры для тестов ProjectX.
🔥 ИСПРАВЛЕНО: Устанавливаем переменные окружения ДО импорта Settings.
"""
import asyncio
import os
import sys
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet

# 🔥 КРИТИЧНО: Устанавливаем env vars ДО любого импорта config!
# Это нужно, потому что Settings() инициализируется при первом импорте
os.environ["BOT_TOKEN"] = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
os.environ["ADMIN_IDS"] = "[123456789, 987654321]"  # JSON формат!
os.environ["DB_PATH"] = ":memory:"
os.environ["DB_ENCRYPTION_KEY"] = Fernet.generate_key().decode("utf-8")
os.environ["DEFAULT_DEVICE_LIMIT"] = "2"
os.environ["REFERRAL_BONUS_DAYS"] = "3"
os.environ["SUPPORT_USERNAME"] = "test_support"


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def reset_settings_singleton():
    """Сбрасывает singleton Settings перед каждым тестом"""
    import config.settings
    config.settings._settings = None
    yield
    config.settings._settings = None


@pytest_asyncio.fixture
async def test_db_session():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database.models import Base
    
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def cleanup_http_session():
    """Закрывает глобальную aiohttp сессию после каждого теста"""
    yield
    try:
        from services.amnezia_client import close_http_session
        await close_http_session()
    except Exception:
        pass


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.send_document = AsyncMock(return_value=MagicMock(message_id=3))
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=4))
    bot.send_invoice = AsyncMock(return_value=MagicMock(message_id=5))
    bot.delete_message = AsyncMock()
    bot.get_me = AsyncMock(return_value=MagicMock(username="test_bot", id=123456))
    bot.set_my_commands = AsyncMock()
    bot.set_chat_menu_button = AsyncMock()
    return bot


@pytest.fixture
def sample_vpn_uri():
    """
    🔥 ИСПРАВЛЕНО: Корректный sample_vpn_uri с полным client_priv_key.
    Используется для тестов vpn_parser и device_service.
    """
    import base64, zlib, json
    
    config_data = {
        "containers": [{
            "container": "amnezia-awg2",
            "awg": {
                "protocol_version": "2",
                "port": "1234",
                "transport_proto": "udp",
                "Jc": "4", "Jmin": "10", "Jmax": "50",
                "S1": "79", "S2": "115", "S3": "5", "S4": "1",
                "H1": "169154911-1234371153",
                "H2": "2057051984-2121122945",
                "H3": "2132872968-2133668229",
                "H4": "2136455412-2141801388",
                "I1": "<r 2><b 0x8580>",
                "I2": "", "I3": "", "I4": "", "I5": "",
                "last_config": json.dumps({
                    "config": "[Interface]\nAddress = 10.8.1.34/32\nDNS = 1.1.1.1, 1.0.0.1\nPrivateKey = uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=\nJc = 4\nJmin = 10\nJmax = 50\nS1 = 79\nS2 = 115\nS3 = 5\nS4 = 1\nH1 = 169154911-1234371153\nH2 = 2057051984-2121122945\nH3 = 2132872968-2133668229\nH4 = 2136455412-2141801388\nh1 = <r 2><b 0x8580>\nh2 = \nh3 = \nh4 = \nh5 = \n\n[Peer]\nPublicKey = bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=\nPresharedKey = PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=\nAllowedIPs = 0.0.0.0/0, ::/0\nEndpoint = test.server.com:1234\nPersistentKeepalive = 25",
                    "mtu": "1376",
                    "client_ip": "10.8.1.34",
                    "client_priv_key": "uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=",
                    "client_pub_key": "dwvGfuluZKlNwickCgPb6DLiUE36icqZPiQWX/BHwBk=",
                    "server_pub_key": "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=",
                    "psk_key": "PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=",
                    "hostName": "test.server.com",
                    "port": 1234,
                    "persistent_keep_alive": "25",
                    "allowed_ips": ["0.0.0.0/0", "::/0"]
                })
            }
        }],
        "defaultContainer": "amnezia-awg2",
        "description": "Test Server",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
        "hostName": "test.server.com"
    }
    
    json_bytes = json.dumps(config_data).encode("utf-8")
    header = len(json_bytes).to_bytes(4, "big")
    compressed = zlib.compress(json_bytes)
    payload = base64.urlsafe_b64encode(header + compressed).decode("ascii").rstrip("=")
    return f"vpn://{payload}"


@pytest.fixture
def sample_user():
    from database.models import User
    return User(
        telegram_id=111111111,
        username="testuser",
        first_name="Test User",
        device_limit=2,
        subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        is_banned=False,
    )


@pytest.fixture
def sample_server():
    from database.models import Server
    return Server(
        name="Test Server",
        country_flag="🇩🇪",
        api_url="http://test.server:4001",
        api_key="test_key_12345678",
        protocol="amneziawg2",
        max_clients=50,
        is_active=True,
    )


@pytest.fixture
def sample_tariff():
    from database.models import Tariff
    return Tariff(
        duration_days=30,
        device_limit=2,
        price_rub=100,
        price_stars=100,
        is_active=True,
        sort_order=10,
    )