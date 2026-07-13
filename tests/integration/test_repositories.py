"""Integration тесты для репозиториев БД."""
import pytest
from datetime import datetime, timezone, timedelta


class TestUsersRepository:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_user(self, test_db_session):
        from database.repositories.users_repo import create_user, get_user_by_telegram_id
        
        user = await create_user(
            test_db_session,
            telegram_id=111222333,
            username="testuser",
            first_name="Test User"
        )
        
        assert user is not None
        assert user.telegram_id == 111222333
        assert user.username == "testuser"
        
        fetched = await get_user_by_telegram_id(test_db_session, 111222333)
        assert fetched is not None
        assert fetched.id == user.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_extend_subscription(self, test_db_session):
        from database.repositories.users_repo import create_user, extend_subscription
        
        user = await create_user(test_db_session, telegram_id=222333444)
        
        extended = await extend_subscription(test_db_session, user, 30)
        
        assert extended.subscription_end is not None
        assert extended.subscription_end > datetime.now(timezone.utc).replace(tzinfo=None)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_user_count(self, test_db_session):
        from database.repositories.users_repo import create_user, get_user_count
        
        initial_count = await get_user_count(test_db_session)
        
        await create_user(test_db_session, telegram_id=333444555)
        await create_user(test_db_session, telegram_id=444555666)
        
        new_count = await get_user_count(test_db_session)
        assert new_count == initial_count + 2


class TestTariffsRepository:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_tariff(self, test_db_session):
        from database.repositories.tariffs_repo import create_tariff, get_tariff_by_id
        
        tariff = await create_tariff(
            test_db_session,
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100
        )
        
        assert tariff is not None
        assert tariff.duration_days == 30
        assert tariff.device_limit == 2
        
        fetched = await get_tariff_by_id(test_db_session, tariff.id)
        assert fetched is not None
        assert fetched.price_rub == 100

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_active_tariffs(self, test_db_session):
        from database.repositories.tariffs_repo import create_tariff, get_active_tariffs, update_tariff
        
        active = await create_tariff(
            test_db_session, duration_days=30, device_limit=2,
            price_rub=100, price_stars=100
        )
        
        inactive = await create_tariff(
            test_db_session, duration_days=30, device_limit=5,
            price_rub=200, price_stars=200
        )
        await update_tariff(test_db_session, inactive, is_active=False)
        
        active_tariffs = await get_active_tariffs(test_db_session)
        
        assert len(active_tariffs) >= 1
        assert any(t.id == active.id for t in active_tariffs)
        assert not any(t.id == inactive.id for t in active_tariffs)


class TestServersRepository:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_server(self, test_db_session):
        from database.repositories.servers_repo import create_server, get_server_by_id
        
        server = await create_server(
            test_db_session,
            name="Test Server",
            api_url="http://test.server:4001",
            api_key="test_key_12345678",
            protocol="amneziawg2",
            max_clients=50
        )
        
        assert server is not None
        assert server.name == "Test Server"
        assert server.max_clients == 50
        
        fetched = await get_server_by_id(test_db_session, server.id)
        assert fetched is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_available_servers(self, test_db_session):
        from database.repositories.servers_repo import create_server, get_available_servers
        
        # ИСПРАВЛЕНО: убран is_active=True (по умолчанию уже True в модели)
        server = await create_server(
            test_db_session,
            name="Available Server",
            api_url="http://available.server:4001",
            api_key="test_key_available",
            protocol="amneziawg2",
            max_clients=50
        )
        
        available = await get_available_servers(test_db_session)
        
        assert len(available) >= 1
        assert any(s.id == server.id for s in available)


class TestProfilesRepository:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_profile(self, test_db_session):
        from database.repositories.users_repo import create_user
        from database.repositories.servers_repo import create_server
        from database.repositories.profiles_repo import create_profile, get_user_profiles
        
        user = await create_user(test_db_session, telegram_id=555666777)
        server = await create_server(
            test_db_session,
            name="Profile Server",
            api_url="http://profile.server:4001",
            api_key="test_key_profile",
            protocol="amneziawg2"
        )
        
        profile = await create_profile(
            test_db_session,
            user_id=user.id,
            server_id=server.id,
            device_name="Test Device",
            peer_id="test_peer_id",
            raw_config="vpn://test_config"
        )
        
        assert profile is not None
        assert profile.device_name == "Test Device"
        
        profiles = await get_user_profiles(test_db_session, user.id)
        assert len(profiles) == 1
        assert profiles[0].id == profile.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_profile(self, test_db_session):
        from database.repositories.users_repo import create_user
        from database.repositories.servers_repo import create_server
        from database.repositories.profiles_repo import create_profile, update_profile
        
        user = await create_user(test_db_session, telegram_id=666777888)
        server = await create_server(
            test_db_session,
            name="Update Server",
            api_url="http://update.server:4001",
            api_key="test_key_update",
            protocol="amneziawg2"
        )
        
        profile = await create_profile(
            test_db_session,
            user_id=user.id,
            server_id=server.id,
            device_name="Old Name",
            peer_id="test_peer",
            raw_config="vpn://config"
        )
        
        updated = await update_profile(
            test_db_session, profile,
            device_name="New Name",
            traffic_down=1000,
            traffic_up=500
        )
        
        assert updated.device_name == "New Name"
        assert updated.traffic_down == 1000
        assert updated.traffic_up == 500


class TestPaymentsRepository:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_payment(self, test_db_session):
        from database.repositories.users_repo import create_user
        from database.repositories.tariffs_repo import create_tariff
        from database.repositories.payments_repo import create_payment, get_payment_by_id
        
        user = await create_user(test_db_session, telegram_id=777888999)
        tariff = await create_tariff(
            test_db_session, duration_days=30, device_limit=2,
            price_rub=100, price_stars=100
        )
        
        payment = await create_payment(
            test_db_session,
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="rub"
        )
        
        assert payment is not None
        assert payment.status == "pending"
        
        fetched = await get_payment_by_id(test_db_session, payment.id)
        assert fetched is not None
        assert fetched.amount == 100
