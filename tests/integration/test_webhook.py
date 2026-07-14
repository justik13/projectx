"""Integration тесты для webhook Platega."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop


class TestPlategaWebhook:
    """Тесты обработчика webhook от Platega.io"""

    def _make_mock_session_scope(self, test_db_session):
        """Создаёт mock для session_scope, возвращающий test_db_session"""
        from contextlib import asynccontextmanager
        
        @asynccontextmanager
        async def mock_scope():
            yield test_db_session
        
        return mock_scope

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_invalid_credentials(self, test_db_session):
        """Webhook с невалидными credentials должен вернуть 401"""
        from bot.handlers.webhook import platega_webhook_handler
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "invalid_merchant",
            "X-Secret": "invalid_secret"
        }
        request.json = AsyncMock(return_value={
            "id": "test_transaction",
            "status": "CONFIRMED"
        })
        
        with patch('bot.handlers.webhook.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.validate_callback = MagicMock(return_value=False)
            MockClient.return_value = mock_instance
            
            response = await platega_webhook_handler(request)
            
            assert response.status == 401
            assert "Unauthorized" in response.text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_invalid_json(self, test_db_session):
        """Webhook с некорректным JSON должен вернуть 400"""
        from bot.handlers.webhook import platega_webhook_handler
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(side_effect=Exception("Invalid JSON"))
        
        with patch('bot.handlers.webhook.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.validate_callback = MagicMock(return_value=True)
            MockClient.return_value = mock_instance
            
            response = await platega_webhook_handler(request)
            
            assert response.status == 400
            assert "Invalid JSON" in response.text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_missing_required_fields(self, test_db_session):
        """Webhook без обязательных полей должен вернуть 400"""
        from bot.handlers.webhook import platega_webhook_handler
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(return_value={})  # Нет id и status
        
        with patch('bot.handlers.webhook.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.validate_callback = MagicMock(return_value=True)
            MockClient.return_value = mock_instance
            
            response = await platega_webhook_handler(request)
            
            assert response.status == 400
            assert "Missing required fields" in response.text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_payment_not_found(self, test_db_session):
        """Webhook для несуществующего платежа должен вернуть 404"""
        from bot.handlers.webhook import platega_webhook_handler
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(return_value={
            "id": "nonexistent_transaction",
            "status": "CONFIRMED"
        })
        
        mock_scope = self._make_mock_session_scope(test_db_session)
        
        with patch('bot.handlers.webhook.session_scope', side_effect=mock_scope):
            with patch('bot.handlers.webhook.PlategaClient') as MockClient:
                mock_instance = MagicMock()
                mock_instance.validate_callback = MagicMock(return_value=True)
                MockClient.return_value = mock_instance
                
                response = await platega_webhook_handler(request)
                
                assert response.status == 404
                assert "Payment not found" in response.text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_confirmed_success(self, test_db_session):
        """Webhook со статусом CONFIRMED должен вернуть 200 при успехе"""
        from bot.handlers.webhook import platega_webhook_handler
        from database.models import User, Tariff, Payment
        from datetime import datetime, timezone, timedelta
        
        # Создаём тестовые данные
        user = User(telegram_id=111111111, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()
        
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()
        
        payment = Payment(
            user_id=user.id, tariff_id=tariff.id,
            amount=100, currency="RUB", status="pending",
            external_id="test_transaction_123"
        )
        test_db_session.add(payment)
        await test_db_session.commit()
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(return_value={
            "id": "test_transaction_123",
            "status": "CONFIRMED"
        })
        
        mock_scope = self._make_mock_session_scope(test_db_session)
        
        with patch('bot.handlers.webhook.session_scope', side_effect=mock_scope):
            with patch('bot.handlers.webhook.PlategaClient') as MockClient:
                mock_instance = MagicMock()
                mock_instance.validate_callback = MagicMock(return_value=True)
                MockClient.return_value = mock_instance
                
                response = await platega_webhook_handler(request)
                
                assert response.status == 200
                assert response.text == "OK"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_status_normalization(self, test_db_session):
        """Webhook должен нормализовать статус через .upper()"""
        from bot.handlers.webhook import platega_webhook_handler
        from database.models import User, Tariff, Payment
        
        # Создаём тестовые данные
        user = User(telegram_id=222222222, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()
        
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()
        
        payment = Payment(
            user_id=user.id, tariff_id=tariff.id,
            amount=100, currency="RUB", status="pending",
            external_id="test_transaction_456"
        )
        test_db_session.add(payment)
        await test_db_session.commit()
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        # 🔥 Тестируем lowercase статус — код должен нормализовать через .upper()
        request.json = AsyncMock(return_value={
            "id": "test_transaction_456",
            "status": "confirmed"  # lowercase!
        })
        
        mock_scope = self._make_mock_session_scope(test_db_session)
        
        with patch('bot.handlers.webhook.session_scope', side_effect=mock_scope):
            with patch('bot.handlers.webhook.PlategaClient') as MockClient:
                mock_instance = MagicMock()
                mock_instance.validate_callback = MagicMock(return_value=True)
                MockClient.return_value = mock_instance
                
                response = await platega_webhook_handler(request)
                
                # 🔥 ИСПРАВЛЕНО: Код нормализует статус, поэтому должен вернуть 200
                assert response.status == 200

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_canceled(self, test_db_session):
        """Webhook со статусом CANCELED должен вернуть 200"""
        from bot.handlers.webhook import platega_webhook_handler
        from database.models import User, Tariff, Payment
        
        user = User(telegram_id=333333333, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()
        
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()
        
        payment = Payment(
            user_id=user.id, tariff_id=tariff.id,
            amount=100, currency="RUB", status="pending",
            external_id="test_transaction_789"
        )
        test_db_session.add(payment)
        await test_db_session.commit()
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(return_value={
            "id": "test_transaction_789",
            "status": "CANCELED"
        })
        
        mock_scope = self._make_mock_session_scope(test_db_session)
        
        with patch('bot.handlers.webhook.session_scope', side_effect=mock_scope):
            with patch('bot.handlers.webhook.PlategaClient') as MockClient:
                mock_instance = MagicMock()
                mock_instance.validate_callback = MagicMock(return_value=True)
                MockClient.return_value = mock_instance
                
                response = await platega_webhook_handler(request)
                
                assert response.status == 200
                assert response.text == "OK"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_chargeback(self, test_db_session):
        """Webhook со статусом CHARGEBACKED должен вернуть 200"""
        from bot.handlers.webhook import platega_webhook_handler
        from database.models import User, Tariff, Payment
        
        user = User(telegram_id=444444444, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()
        
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()
        
        payment = Payment(
            user_id=user.id, tariff_id=tariff.id,
            amount=100, currency="RUB", status="pending",
            external_id="test_transaction_abc"
        )
        test_db_session.add(payment)
        await test_db_session.commit()
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(return_value={
            "id": "test_transaction_abc",
            "status": "CHARGEBACKED"
        })
        
        mock_scope = self._make_mock_session_scope(test_db_session)
        
        with patch('bot.handlers.webhook.session_scope', side_effect=mock_scope):
            with patch('bot.handlers.webhook.PlategaClient') as MockClient:
                mock_instance = MagicMock()
                mock_instance.validate_callback = MagicMock(return_value=True)
                MockClient.return_value = mock_instance
                
                response = await platega_webhook_handler(request)
                
                assert response.status == 200
                assert response.text == "OK"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_webhook_internal_error(self, test_db_session):
        """Webhook с внутренней ошиббкой должен вернуть 500"""
        from bot.handlers.webhook import platega_webhook_handler
        
        request = MagicMock()
        request.headers = {
            "X-MerchantId": "test_merchant",
            "X-Secret": "test_secret"
        }
        request.json = AsyncMock(side_effect=Exception("Database error"))
        
        with patch('bot.handlers.webhook.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.validate_callback = MagicMock(return_value=True)
            MockClient.return_value = mock_instance
            
            response = await platega_webhook_handler(request)
            
            assert response.status == 500
            assert "Internal server error" in response.text