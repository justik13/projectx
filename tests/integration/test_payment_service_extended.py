"""Расширенные integration тесты для PaymentService — Platega операции."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestPlategaPaymentCreation:
    """Тесты создания Platega платежей."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_platega_payment_success(self, test_db_session):
        """Успешное создание Platega платежа."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff

        user = User(telegram_id=555555555, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        mock_transaction = {
            "transactionId": "tx-platega-001",
            "redirect": "https://pay.platega.io/tx-platega-001",
            "status": "PENDING",
            "paymentMethod": "SBPQR"
        }

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_transaction = AsyncMock(return_value=mock_transaction)
            MockClient.return_value = mock_instance

            payment, error = await PaymentService.create_platega_payment(
                session=test_db_session,
                user_id=user.id,
                tariff_id=tariff.id,
                amount=100.0,
                telegram_id=user.telegram_id,
                bot_username="test_bot"
            )

        assert payment is not None
        assert error is None
        assert payment.external_id == "tx-platega-001"
        assert payment.payment_url == "https://pay.platega.io/tx-platega-001"
        assert payment.payment_method == "SBPQR"
        assert payment.status == "pending"
        assert payment.amount == 100

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_platega_payment_api_failure(self, test_db_session):
        """API ошибка при создании Platega платежа."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff

        user = User(telegram_id=666666666, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=200, price_stars=200, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_transaction = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            payment, error = await PaymentService.create_platega_payment(
                session=test_db_session,
                user_id=user.id,
                tariff_id=tariff.id,
                amount=200.0,
                telegram_id=user.telegram_id,
                bot_username="test_bot"
            )

        assert payment is not None
        assert error is None
        assert payment.status == "failed"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_platega_payment_bot_username_strip(self, test_db_session):
        """Username бота корректно очищается от @."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff

        user = User(telegram_id=777777777, device_limit=2)
        test_db_session.add(user)
        await test_db_session.commit()

        tariff = Tariff(
            duration_days=7, device_limit=2,
            price_rub=50, price_stars=50, is_active=True
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        mock_transaction = {
            "transactionId": "tx-strip-001",
            "redirect": "https://pay.platega.io/tx-strip-001",
            "status": "PENDING"
        }

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_transaction = AsyncMock(return_value=mock_transaction)
            MockClient.return_value = mock_instance

            payment, _ = await PaymentService.create_platega_payment(
                session=test_db_session,
                user_id=user.id,
                tariff_id=tariff.id,
                amount=50.0,
                telegram_id=user.telegram_id,
                bot_username="@my_bot"
            )

        assert payment is not None
        assert payment.status == "pending"


class TestPlategaCallbackHandling:
    """Тесты обработки Platega callback."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_confirmed(self, test_db_session):
        """CONFIRMED callback продлевает подписку."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

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
            external_id="tx-callback-001"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-callback-001",
            status="CONFIRMED",
            payload=""
        )

        assert success is True
        assert code == "success"

        await test_db_session.refresh(payment)
        assert payment.status == "completed"

        await test_db_session.refresh(user)
        assert user.subscription_end is not None
        assert user.subscription_end > datetime.now(timezone.utc).replace(tzinfo=None)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_canceled(self, test_db_session):
        """CANCELED callback помечает платёж как cancelled."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

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
            external_id="tx-canceled-001"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-canceled-001",
            status="CANCELED",
            payload=""
        )

        assert success is True
        assert code == "success"

        await test_db_session.refresh(payment)
        assert payment.status == "cancelled"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_chargebacked(self, test_db_session):
        """CHARGEBACKED callback помечает платёж как refunded."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(
            telegram_id=333333333, device_limit=2,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        )
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
            amount=100, currency="RUB", status="completed",
            external_id="tx-chargeback-001",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-chargeback-001",
            status="CHARGEBACKED",
            payload=""
        )

        assert success is True
        assert code == "success"

        await test_db_session.refresh(payment)
        assert payment.status == "refunded"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_not_found(self, test_db_session):
        """Callback для несуществующего платежа возвращает not_found."""
        from services.payment_service import PaymentService

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="nonexistent-tx",
            status="CONFIRMED",
            payload=""
        )

        assert success is False
        assert code == "not_found"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_unknown_status(self, test_db_session):
        """Callback с неизвестным статусом возвращает error."""
        from services.payment_service import PaymentService
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
            external_id="tx-unknown-001"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-unknown-001",
            status="UNKNOWN_STATUS",
            payload=""
        )

        assert success is False
        assert code == "error"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_canceled_idempotent(self, test_db_session):
        """Повторный CANCELED callback возвращает already_processed."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=555555001, device_limit=2)
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
            amount=100, currency="RUB", status="cancelled",
            external_id="tx-idem-cancel"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-idem-cancel",
            status="CANCELED",
            payload=""
        )

        assert success is True
        assert code == "already_processed"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_callback_chargebacked_idempotent(self, test_db_session):
        """Повторный CHARGEBACKED callback возвращает already_processed."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=555555002, device_limit=2)
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
            amount=100, currency="RUB", status="refunded",
            external_id="tx-idem-cb",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-idem-cb",
            status="CHARGEBACKED",
            payload=""
        )

        assert success is True
        assert code == "already_processed"


class TestCheckPlategaPayment:
    """Тесты проверки статуса Platega платежа."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_confirmed(self, test_db_session):
        """Проверка CONFIRMED статуса продлевает подписку."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666001, device_limit=2)
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
            external_id="tx-check-001"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-001",
                "status": "CONFIRMED",
                "amount": 100.0
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is True

        await test_db_session.refresh(payment)
        assert payment.status == "completed"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_pending(self, test_db_session):
        """Проверка PENDING статуса возвращает False."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666002, device_limit=2)
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
            external_id="tx-check-002"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-002",
                "status": "PENDING",
                "amount": 100.0
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        await test_db_session.refresh(payment)
        assert payment.status == "pending"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_canceled(self, test_db_session):
        """Проверка CANCELED статуса помечает платёж как cancelled."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666003, device_limit=2)
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
            external_id="tx-check-003"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-003",
                "status": "CANCELED",
                "amount": 100.0
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        await test_db_session.refresh(payment)
        assert payment.status == "cancelled"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_no_external_id(self, test_db_session):
        """Платёж без external_id возвращает False."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666004, device_limit=2)
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
            amount=100, currency="stars", status="pending",
            external_id=None
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        result = await PaymentService.check_platega_payment(
            test_db_session, payment.id
        )

        assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_already_completed(self, test_db_session):
        """Уже completed платёж возвращает True."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666005, device_limit=2)
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
            amount=100, currency="RUB", status="completed",
            external_id="tx-already-done",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        result = await PaymentService.check_platega_payment(
            test_db_session, payment.id
        )

        assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_api_returns_none(self, test_db_session):
        """API вернул None — возвращает False."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=666666006, device_limit=2)
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
            external_id="tx-api-none"
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value=None)
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        await test_db_session.refresh(payment)
        assert payment.status == "pending"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_check_payment_not_found(self, test_db_session):
        """Несуществующий платёж возвращает False."""
        from services.payment_service import PaymentService

        result = await PaymentService.check_platega_payment(
            test_db_session, 99999
        )

        assert result is False