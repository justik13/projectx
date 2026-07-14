"""Integration тесты для сервиса устройств."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from services.amnezia_client import AmneziaClientCreateResponse


class TestDeviceService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_device_success(self, test_db_session, sample_user, sample_server):
        from services.device_service import DeviceService
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        # 🔥 ИСПРАВЛЕНО: мок возвращает DTO AmneziaClientCreateResponse
        mock_dto = AmneziaClientCreateResponse(
            id="test_peer_id_12345",
            config="vpn://test_config_data",
            protocol="amneziawg2"
        )
        
        with patch('services.device_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_user = AsyncMock(return_value=mock_dto)
            MockClient.return_value = mock_instance
            
            with patch('services.device_service.SubscriptionService.get_expires_timestamp',
                       return_value=1234567890):
                with patch('services.device_service.is_valid_vpn_uri', return_value=True):
                    with patch('services.device_service.decode_vpn_uri_to_json', return_value={}):
                        with patch('services.device_service.validate_awg2_config') as mock_validate:
                            mock_validate_result = MagicMock()
                            mock_validate_result.is_valid = True
                            mock_validate_result.errors = []
                            mock_validate.return_value = mock_validate_result
                            
                            profile = await DeviceService.create_device(
                                test_db_session, sample_user, sample_server.id, "TestDevice"
                            )
                            
                            assert profile is not None
                            assert profile.device_name == "TestDevice"
                            assert profile.peer_id == "test_peer_id_12345"
                            assert profile.raw_config == "vpn://test_config_data"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_device_limit_reached(self, test_db_session, sample_user, sample_server):
        from services.device_service import DeviceService
        from database.models import VPNProfile
        
        sample_user.device_limit = 1
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        # Создаём первое устройство напрямую в БД
        profile1 = VPNProfile(
            user_id=sample_user.id,
            server_id=sample_server.id,
            device_name="Device1",
            peer_id="peer_1",
            raw_config="vpn://config1",
        )
        test_db_session.add(profile1)
        await test_db_session.commit()
        
        # Пытаемся создать второе — должно вернуть None
        with patch('services.device_service.AmneziaClient'):
            profile2 = await DeviceService.create_device(
                test_db_session, sample_user, sample_server.id, "Device2"
            )
            assert profile2 is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_device_invalid_protocol(self, test_db_session, sample_user):
        from services.device_service import DeviceService
        from database.models import Server
        
        server = Server(
            name="Bad Server",
            api_url="http://bad.server:4001",
            api_key="test_key",
            protocol="wireguard",  # Неправильный протокол!
            max_clients=50,
            is_active=True,
        )
        test_db_session.add(sample_user)
        test_db_session.add(server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(server)
        
        profile = await DeviceService.create_device(
            test_db_session, sample_user, server.id, "TestDevice"
        )
        assert profile is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_device_api_failure(self, test_db_session, sample_user, sample_server):
        from services.device_service import DeviceService
        
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        with patch('services.device_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_user = AsyncMock(return_value=None)  # API ошибка
            MockClient.return_value = mock_instance
            
            with patch('services.device_service.SubscriptionService.get_expires_timestamp',
                       return_value=1234567890):
                profile = await DeviceService.create_device(
                    test_db_session, sample_user, sample_server.id, "TestDevice"
                )
                assert profile is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_device_invalid_vpn_uri(self, test_db_session, sample_user, sample_server):
        """Тест отката при невалидном vpn:// URI от API."""
        from services.device_service import DeviceService
        
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        # 🔥 ИСПРАВЛЕНО: мок возвращает DTO с невалидным конфигом
        mock_dto = AmneziaClientCreateResponse(
            id="test_peer_id",
            config="invalid_not_vpn_uri",
            protocol="amneziawg2"
        )
        
        with patch('services.device_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_user = AsyncMock(return_value=mock_dto)
            mock_instance.delete_user = AsyncMock(return_value=True)
            MockClient.return_value = mock_instance
            
            with patch('services.device_service.SubscriptionService.get_expires_timestamp',
                       return_value=1234567890):
                with patch('services.device_service.is_valid_vpn_uri', return_value=False):
                    with patch('services.device_service.AuditService.log_action'):
                        profile = await DeviceService.create_device(
                            test_db_session, sample_user, sample_server.id, "TestDevice"
                        )
                        
                        # Должен вернуть None и откатить через delete_user
                        assert profile is None
                        mock_instance.delete_user.assert_called_once_with(client_id="test_peer_id")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_delete_device_success(self, test_db_session, sample_user, sample_server):
        from services.device_service import DeviceService
        from database.models import VPNProfile
        
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        profile = VPNProfile(
            user_id=sample_user.id,
            server_id=sample_server.id,
            device_name="ToDelete",
            peer_id="peer_to_delete",
            raw_config="vpn://config",
        )
        test_db_session.add(profile)
        await test_db_session.commit()
        await test_db_session.refresh(profile)
        
        with patch('services.device_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.delete_user = AsyncMock(return_value=True)
            MockClient.return_value = mock_instance
            
            result = await DeviceService.delete_device(test_db_session, profile)
            assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_delete_device_api_failure(self, test_db_session, sample_user, sample_server):
        from services.device_service import DeviceService
        from database.models import VPNProfile
        
        test_db_session.add(sample_user)
        test_db_session.add(sample_server)
        await test_db_session.commit()
        await test_db_session.refresh(sample_user)
        await test_db_session.refresh(sample_server)
        
        profile = VPNProfile(
            user_id=sample_user.id,
            server_id=sample_server.id,
            device_name="ToDelete",
            peer_id="peer_to_delete",
            raw_config="vpn://config",
        )
        test_db_session.add(profile)
        await test_db_session.commit()
        await test_db_session.refresh(profile)
        
        with patch('services.device_service.AmneziaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.delete_user = AsyncMock(return_value=False)  # API ошибка
            MockClient.return_value = mock_instance
            
            result = await DeviceService.delete_device(test_db_session, profile)
            assert result is False