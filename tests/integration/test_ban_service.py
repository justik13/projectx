"""Integration тесты для сервиса банов."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock


class TestBanService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_toggle_ban_user(self, test_db_session):
        from services.ban_service import BanService
        from database.models import User

        user = User(
            telegram_id=555555555,
            is_banned=False,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        success, result = await BanService.toggle_ban(test_db_session, 123456789, 555555555)
        
        assert success is True
        assert result == "забанен"
        
        await test_db_session.refresh(user)
        assert user.is_banned is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_toggle_unban_user(self, test_db_session):
        from services.ban_service import BanService
        from database.models import User

        user = User(
            telegram_id=666666666,
            is_banned=True,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        success, result = await BanService.toggle_ban(test_db_session, 123456789, 666666666)
        
        assert success is True
        assert result == "разбанен"
        
        await test_db_session.refresh(user)
        assert user.is_banned is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_toggle_ban_user_not_found(self, test_db_session):
        from services.ban_service import BanService

        success, result = await BanService.toggle_ban(test_db_session, 123456789, 999999999)
        
        assert success is False
        assert result == "Пользователь не найден"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_toggle_ban_with_profiles(self, test_db_session):
        from services.ban_service import BanService
        from database.models import User, Server, VPNProfile

        user = User(
            telegram_id=777777777,
            is_banned=False,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        server = Server(
            name="Ban Test Server",
            api_url="http://ban.test:4001",
            api_key="test_key",
            protocol="amneziawg2",
            max_clients=50,
            is_active=True,
        )
        test_db_session.add(user)
        test_db_session.add(server)
        await test_db_session.commit()
        await test_db_session.refresh(user)
        await test_db_session.refresh(server)

        profile = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name="Device",
            peer_id="peer_123",
            raw_config="vpn://config",
            is_active=True,
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        with patch('services.ban_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.update_client = AsyncMock(return_value=True)
            MockClient.return_value = mock_instance

            success, result = await BanService.toggle_ban(test_db_session, 123456789, 777777777)
            
            assert success is True
            assert result == "забанен"
            
            await test_db_session.refresh(profile)
            assert profile.is_active is False
