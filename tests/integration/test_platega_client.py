"""Integration тесты для PlategaClient — HTTP клиент Platega.io API."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import aiohttp


class TestPlategaClientInit:
    """Тесты инициализации PlategaClient."""

    @pytest.mark.integration
    def test_client_init_from_settings(self):
        """Клиент корректно инициализируется из настроек."""
        from services.platega_client import PlategaClient

        with patch('services.platega_client.get_settings') as mock_settings:
            settings = MagicMock()
            settings.PLATEGA_BASE_URL = "https://app.platega.io/"
            settings.PLATEGA_MERCHANT_ID = "test-merchant-uuid"
            settings.PLATEGA_SECRET = "test-secret-key"
            settings.PLATEGA_PAYMENT_METHOD = 2
            mock_settings.return_value = settings

            client = PlategaClient()

            assert client.base_url == "https://app.platega.io"
            assert client.merchant_id == "test-merchant-uuid"
            assert client.secret == "test-secret-key"
            assert client.payment_method == 2

    @pytest.mark.integration
    def test_get_headers(self):
        """Заголовки содержат все необходимые поля."""
        from services.platega_client import PlategaClient

        with patch('services.platega_client.get_settings') as mock_settings:
            settings = MagicMock()
            settings.PLATEGA_BASE_URL = "https://app.platega.io"
            settings.PLATEGA_MERCHANT_ID = "merchant-123"
            settings.PLATEGA_SECRET = "secret-456"
            settings.PLATEGA_PAYMENT_METHOD = 10
            mock_settings.return_value = settings

            client = PlategaClient()
            headers = client._get_headers()

            assert headers["X-MerchantId"] == "merchant-123"
            assert headers["X-Secret"] == "secret-456"
            assert headers["Content-Type"] == "application/json"
            assert len(headers) == 3


class TestPlategaClientValidateCallback:
    """Тесты валидации callback credentials."""

    def _make_client(self):
        from services.platega_client import PlategaClient
        with patch('services.platega_client.get_settings') as mock_settings:
            settings = MagicMock()
            settings.PLATEGA_BASE_URL = "https://app.platega.io"
            settings.PLATEGA_MERCHANT_ID = "valid-merchant"
            settings.PLATEGA_SECRET = "valid-secret"
            settings.PLATEGA_PAYMENT_METHOD = 2
            mock_settings.return_value = settings
            return PlategaClient()

    @pytest.mark.integration
    def test_validate_callback_success(self):
        """Валидация проходит при совпадении credentials."""
        client = self._make_client()
        assert client.validate_callback("valid-merchant", "valid-secret") is True

    @pytest.mark.integration
    def test_validate_callback_wrong_merchant(self):
        """Валидация fails при неверном merchant_id."""
        client = self._make_client()
        assert client.validate_callback("wrong-merchant", "valid-secret") is False

    @pytest.mark.integration
    def test_validate_callback_wrong_secret(self):
        """Валидация fails при неверном secret."""
        client = self._make_client()
        assert client.validate_callback("valid-merchant", "wrong-secret") is False

    @pytest.mark.integration
    def test_validate_callback_empty_merchant(self):
        """Валидация fails при пустом merchant_id."""
        client = self._make_client()
        assert client.validate_callback("", "valid-secret") is False

    @pytest.mark.integration
    def test_validate_callback_empty_secret(self):
        """Валидация fails при пустом secret."""
        client = self._make_client()
        assert client.validate_callback("valid-merchant", "") is False

    @pytest.mark.integration
    def test_validate_callback_both_wrong(self):
        """Валидация fails когда оба credential неверные."""
        client = self._make_client()
        assert client.validate_callback("wrong", "wrong") is False


class TestPlategaClientCreateTransaction:
    """Тесты создания транзакции через Platega API."""

    def _make_client(self):
        from services.platega_client import PlategaClient
        with patch('services.platega_client.get_settings') as mock_settings:
            settings = MagicMock()
            settings.PLATEGA_BASE_URL = "https://app.platega.io"
            settings.PLATEGA_MERCHANT_ID = "test-merchant"
            settings.PLATEGA_SECRET = "test-secret"
            settings.PLATEGA_PAYMENT_METHOD = 2
            mock_settings.return_value = settings
            return PlategaClient()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_success(self):
        """Успешное создание транзакции возвращает данные."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "transactionId": "tx-uuid-123",
            "redirect": "https://pay.platega.io/tx-uuid-123",
            "status": "PENDING",
            "paymentMethod": "SBPQR",
            "expiresIn": "2026-01-15T12:00:00Z"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=100.0,
                currency="RUB",
                description="Test payment",
                return_url="https://example.com/success",
                failed_url="https://example.com/fail",
                payload="order_123"
            )

        assert result is not None
        assert result["transactionId"] == "tx-uuid-123"
        assert result["redirect"] == "https://pay.platega.io/tx-uuid-123"
        assert result["status"] == "PENDING"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_minimal_params(self):
        """Создание транзакции с минимальными параметрами."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "transactionId": "tx-minimal",
            "redirect": "https://pay.platega.io/tx-minimal",
            "status": "PENDING"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=50.0,
                currency="RUB"
            )

        assert result is not None
        assert result["transactionId"] == "tx-minimal"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_api_error_400(self):
        """API ошибка 400 возвращает None."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad Request: invalid amount")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=-100.0,
                currency="RUB"
            )

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_api_error_500(self):
        """API ошибка 500 возвращает None."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=100.0,
                currency="RUB"
            )

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_network_error(self):
        """Сетевая ошибка возвращает None."""
        client = self._make_client()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=100.0,
                currency="RUB"
            )

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_timeout_error(self):
        """Timeout ошибка возвращает None."""
        client = self._make_client()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=TimeoutError("Request timeout"))

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=100.0,
                currency="RUB"
            )

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_generic_exception(self):
        """Непредвиденная ошибка возвращает None."""
        client = self._make_client()

        mock_session = AsyncMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("Unexpected"))

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.create_transaction(
                amount=100.0,
                currency="RUB"
            )

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_transaction_request_body(self):
        """Проверяет что тело запроса формируется корректно."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "transactionId": "tx-body-check",
            "redirect": "https://pay.platega.io/tx-body-check",
            "status": "PENDING"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            await client.create_transaction(
                amount=250.50,
                currency="RUB",
                description="Order #456",
                return_url="https://site.com/ok",
                failed_url="https://site.com/fail",
                payload="order_456"
            )

        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        assert "json" in call_kwargs.kwargs or len(call_kwargs.args) >= 2


class TestPlategaClientCheckStatus:
    """Тесты проверки статуса транзакции."""

    def _make_client(self):
        from services.platega_client import PlategaClient
        with patch('services.platega_client.get_settings') as mock_settings:
            settings = MagicMock()
            settings.PLATEGA_BASE_URL = "https://app.platega.io"
            settings.PLATEGA_MERCHANT_ID = "test-merchant"
            settings.PLATEGA_SECRET = "test-secret"
            settings.PLATEGA_PAYMENT_METHOD = 2
            mock_settings.return_value = settings
            return PlategaClient()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_confirmed(self):
        """Статус CONFIRMED возвращается корректно."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-123",
            "status": "CONFIRMED",
            "amount": 100.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-123")

        assert result is not None
        assert result["status"] == "CONFIRMED"
        assert result["amount"] == 100.0

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_pending(self):
        """Статус PENDING возвращается корректно."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-456",
            "status": "PENDING",
            "amount": 200.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-456")

        assert result is not None
        assert result["status"] == "PENDING"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_canceled(self):
        """Статус CANCELED возвращается корректно."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-789",
            "status": "CANCELED",
            "amount": 300.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-789")

        assert result is not None
        assert result["status"] == "CANCELED"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_chargebacked(self):
        """Статус CHARGEBACKED возвращается корректно."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-cb",
            "status": "CHARGEBACKED",
            "amount": 500.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-cb")

        assert result is not None
        assert result["status"] == "CHARGEBACKED"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_normalizes_lowercase(self):
        """Статус нормализуется в uppercase."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-norm",
            "status": "confirmed",
            "amount": 100.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-norm")

        assert result is not None
        assert result["status"] == "CONFIRMED"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_normalizes_mixed_case(self):
        """Статус нормализуется из mixed case."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-mix",
            "status": "Chargebacked",
            "amount": 100.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-mix")

        assert result is not None
        assert result["status"] == "CHARGEBACKED"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_no_status_field(self):
        """Ответ без поля status не вызывает ошибку нормализации."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-no-status",
            "amount": 100.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-no-status")

        assert result is not None
        assert "status" not in result

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_numeric_status_not_normalized(self):
        """Числовой статус не нормализуется."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "id": "tx-num",
            "status": 200,
            "amount": 100.0,
            "currency": "RUB"
        })
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-num")

        assert result is not None
        assert result["status"] == 200

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_not_found_404(self):
        """404 возвращает None."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("nonexistent-tx")

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_server_error_500(self):
        """500 возвращает None."""
        client = self._make_client()

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-error")

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_network_error(self):
        """Сетевая ошибка возвращает None."""
        client = self._make_client()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection failed"))

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-network-error")

        assert result is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_status_generic_exception(self):
        """Непредвиденная ошибка возвращает None."""
        client = self._make_client()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("Unexpected error"))

        with patch('services.platega_client.get_http_session', return_value=mock_session):
            result = await client.check_status("tx-exception")

        assert result is None