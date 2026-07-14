"""Integration тесты для background workers."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from services.amnezia_client import AmneziaClientListItem, AmneziaClientTraffic


class TestCleanupWorker:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cleanup_dangling_peers_no_profiles(self, test_db_session):
        from services.workers.cleanup import cleanup_dangling_peers_loop
        
        with patch('services.workers.cleanup.get_session') as mock_session:
            with patch('services.workers.cleanup.get_active_servers', return_value=[]):
                with patch('services.workers.cleanup.asyncio.sleep', side_effect=[None, SystemExit]):
                    mock_sess = AsyncMock()
                    mock_result = MagicMock()
                    mock_result.all = MagicMock(return_value=[])
                    mock_sess.execute = AsyncMock(return_value=mock_result)
                    mock_session.return_value = mock_sess
                    mock_sess.close = AsyncMock()
                    
                    try:
                        await cleanup_dangling_peers_loop()
                    except SystemExit:
                        pass

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cleanup_dangling_peers_with_phantoms(self, test_db_session):
        """Тест очистки phantom-пиров с DTO AmneziaClientListItem."""
        from services.workers.cleanup import cleanup_dangling_peers_loop
        from database.models import Server, VPNProfile, User
        
        user = User(telegram_id=111111111)
        test_db_session.add(user)
        await test_db_session.commit()
        
        server = Server(
            name="Test Server", api_url="http://test.server:4001",
            api_key="test_key", protocol="amneziawg2", max_clients=50, is_active=True,
        )
        test_db_session.add(server)
        await test_db_session.commit()
        
        profile = VPNProfile(
            user_id=user.id, server_id=server.id, device_name="Device",
            peer_id="peer_in_db", raw_config="vpn://config",
        )
        test_db_session.add(profile)
        await test_db_session.commit()
        
        call_count = 0
        async def mock_get_session():
            nonlocal call_count
            call_count += 1
            mock_sess = AsyncMock()
            if call_count == 1:
                mock_result = MagicMock()
                mock_result.all = MagicMock(return_value=[(profile.id, "peer_in_db")])
                mock_sess.execute = AsyncMock(return_value=mock_result)
            else:
                mock_result = MagicMock()
                mock_result.first = MagicMock(return_value=None)
                mock_sess.execute = AsyncMock(return_value=mock_result)
            mock_sess.close = AsyncMock()
            return mock_sess
        
        # 🔥 ИСПРАВЛЕНО: мок возвращает список DTO AmneziaClientListItem
        dto_in_db = AmneziaClientListItem(
            id="peer_in_db",
            clientName="tg_111_Device_abc",
            status="active",
            traffics=AmneziaClientTraffic(totalDownload=1000, totalUpload=500)
        )
        dto_phantom = AmneziaClientListItem(
            id="phantom_peer",
            clientName="tg_222_Phantom_xyz",
            status="active",
            traffics=AmneziaClientTraffic(totalDownload=2000, totalUpload=1000)
        )
        
        with patch('services.workers.cleanup.get_session', side_effect=mock_get_session):
            with patch('services.workers.cleanup.get_active_servers', return_value=[server]):
                with patch('services.workers.cleanup.AmneziaClient') as MockClient:
                    with patch('services.workers.cleanup.asyncio.sleep', side_effect=[None, SystemExit]):
                        mock_instance = MagicMock()
                        mock_instance.get_all_clients = AsyncMock(return_value=[dto_in_db, dto_phantom])
                        mock_instance.delete_user = AsyncMock(return_value=True)
                        MockClient.return_value = mock_instance
                        
                        try:
                            await cleanup_dangling_peers_loop()
                        except SystemExit:
                            pass
                        
                        # Phantom должен быть удалён
                        mock_instance.delete_user.assert_called_with(client_id="phantom_peer")


class TestNotificationsWorker:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_subscription_notifications_no_users(self, test_db_session, mock_bot):
        from services.workers.notifications import subscription_notifications_loop
        
        with patch('services.workers.notifications.get_session') as mock_session:
            with patch('services.workers.notifications.asyncio.sleep', side_effect=SystemExit):
                mock_sess = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalars = MagicMock(return_value=MagicMock(all=lambda: []))
                mock_sess.execute = AsyncMock(return_value=mock_result)
                mock_sess.close = AsyncMock()
                mock_session.return_value = mock_sess
                
                try:
                    await subscription_notifications_loop(mock_bot)
                except SystemExit:
                    pass

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_subscription_notifications_3_days(self, test_db_session, mock_bot):
        from services.workers.notifications import subscription_notifications_loop
        from database.models import User
        
        user = User(
            telegram_id=333333333,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2),
            notified_3d=False,
        )
        test_db_session.add(user)
        await test_db_session.commit()
        
        call_count = 0
        async def mock_get_session():
            nonlocal call_count
            call_count += 1
            return test_db_session
        
        with patch('services.workers.notifications.get_session', side_effect=mock_get_session):
            with patch('services.workers.notifications.asyncio.sleep', side_effect=[None, SystemExit]):
                mock_bot.send_message = AsyncMock()
                
                try:
                    await subscription_notifications_loop(mock_bot)
                except SystemExit:
                    pass
                
                mock_bot.send_message.assert_called_once()
                assert user.notified_3d is True


class TestPaymentsWorker:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_stale_payments_checker_no_stale(self, test_db_session, mock_bot):
        from services.workers.payments import stale_payments_checker_loop
        
        with patch('services.workers.payments.get_session') as mock_session:
            with patch('services.workers.payments.asyncio.sleep', side_effect=SystemExit):
                mock_sess = AsyncMock()
                mock_result = MagicMock()
                mock_result.scalars = MagicMock(return_value=MagicMock(all=lambda: []))
                mock_sess.execute = AsyncMock(return_value=mock_result)
                mock_sess.close = AsyncMock()
                mock_session.return_value = mock_sess
                
                try:
                    await stale_payments_checker_loop(mock_bot)
                except SystemExit:
                    pass


class TestTrafficWorker:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_traffic_sync_no_servers(self, test_db_session):
        from services.workers.traffic import traffic_sync_loop
        
        with patch('services.workers.traffic.get_session') as mock_session:
            with patch('services.workers.traffic.asyncio.sleep', side_effect=SystemExit):
                mock_sess = AsyncMock()
                mock_result = MagicMock()
                mock_result.all = MagicMock(return_value=[])
                mock_sess.execute = AsyncMock(return_value=mock_result)
                mock_sess.close = AsyncMock()
                mock_session.return_value = mock_sess
                
                try:
                    await traffic_sync_loop()
                except SystemExit:
                    pass

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_traffic_sync_with_data(self, test_db_session):
        """Тест синхронизации трафика с DTO AmneziaClientListItem."""
        from services.workers.traffic import traffic_sync_loop
        from database.models import Server, VPNProfile, User
        
        user = User(telegram_id=444444444)
        test_db_session.add(user)
        await test_db_session.commit()
        
        server = Server(
            name="Traffic Server", api_url="http://traffic.server:4001",
            api_key="test_key", protocol="amneziawg2", max_clients=50, is_active=True,
        )
        test_db_session.add(server)
        await test_db_session.commit()
        
        profile = VPNProfile(
            user_id=user.id, server_id=server.id, device_name="Device",
            peer_id="peer_traffic", raw_config="vpn://config",
            traffic_down=100, traffic_up=50,
        )
        test_db_session.add(profile)
        await test_db_session.commit()
        
        # 🔥 ИСПРАВЛЕНО: мок возвращает dict с DTO
        dto = AmneziaClientListItem(
            id="peer_traffic",
            clientName="tg_444_Device_abc",
            status="active",
            traffics=AmneziaClientTraffic(totalDownload=5000, totalUpload=2500),
            lastHandshake=1700000000.0
        )
        
        call_count = 0
        async def mock_get_session():
            nonlocal call_count
            call_count += 1
            return test_db_session
        
        with patch('services.workers.traffic.get_session', side_effect=mock_get_session):
            with patch('services.workers.traffic.asyncio.sleep', side_effect=[None, SystemExit]):
                with patch('services.workers.traffic.AmneziaClient') as MockClient:
                    mock_instance = MagicMock()
                    mock_instance.get_all_clients = AsyncMock(return_value=[dto])
                    MockClient.return_value = mock_instance
                    
                    try:
                        await traffic_sync_loop()
                    except SystemExit:
                        pass
                    
                    # Проверяем что трафик обновлён
                    await test_db_session.refresh(profile)
                    assert profile.traffic_down == 5000
                    assert profile.traffic_up == 2500