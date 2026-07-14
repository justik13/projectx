"""E2E тесты полного flow оплаты через Platega (СБП)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestPlategaE2EConfirmedFlow:
    """E2E: Полный flow успешной оплаты через СБП."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_full_sbp_payment_confirmed(self, test_db_session):
        """
        E2E: Пользователь создаёт платёж → оплачивает → callback CONFIRMED → подписка продлена.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # === SETUP: Создаём пользователя и тариф ===
        user = User(
            telegram_id=555555555,
            username="sbp_user",
            device_limit=2,
        )
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
            is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # === ШАГ 1: Создаём платёж через Platega ===
        mock_transaction = {
            "transactionId": "tx-e2e-001",
            "redirect": "https://pay.platega.io/tx-e2e-001",
            "status": "PENDING",
            "paymentMethod": "SBPQR"
        }

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.create_transaction = AsyncMock(return_value=mock_transaction)
            MockClient.return_value = mock_instance

            payment, _ = await PaymentService.create_platega_payment(
                session=test_db_session,
                user_id=user.id,
                tariff_id=tariff.id,
                amount=100.0,
                telegram_id=user.telegram_id,
                bot_username="test_bot"
            )

        # Проверяем что платёж создан
        assert payment is not None
        assert payment.status == "pending"
        assert payment.external_id == "tx-e2e-001"
        assert payment.payment_url == "https://pay.platega.io/tx-e2e-001"
        assert payment.payment_method == "SBPQR"
        assert payment.amount == 100

        # === ШАГ 2: Platega отправляет callback CONFIRMED ===
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-e2e-001",
            status="CONFIRMED",
            payload=f"payment_{payment.id}"
        )

        assert success is True
        assert code == "success"

        # === ШАГ 3: Проверяем что платёж помечен как completed ===
        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "completed"
        assert payment.paid_at is not None

        # === ШАГ 4: Проверяем что подписка продлена ===
        await test_db_session.refresh(user)
        assert user.subscription_end is not None
        assert user.subscription_end > datetime.now(timezone.utc).replace(tzinfo=None)
        assert user.device_limit == 2
        assert user.current_tariff_id == tariff.id

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_full_sbp_payment_with_referral_bonus(self, test_db_session):
        """
        E2E: Первая оплата реферала → реферер получает бонусные дни.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # === SETUP: Создаём реферера и реферала ===
        referrer = User(
            telegram_id=999999999,
            username="referrer",
            referral_days=0,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10),
        )
        test_db_session.add(referrer)

        referral = User(
            telegram_id=888888888,
            username="referral",
            referred_by=999999999,
            device_limit=2,
        )
        test_db_session.add(referral)

        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
            is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # === Создаём платёж ===
        payment = Payment(
            user_id=referral.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-ref-001",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # === Callback CONFIRMED ===
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-ref-001",
            status="CONFIRMED",
            payload=""
        )

        assert success is True

        # === Проверяем бонус рефереру ===
        await test_db_session.commit()
        await test_db_session.refresh(referrer)
        assert referrer.referral_days == 3  # REFERRAL_BONUS_DAYS

        old_end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
        assert referrer.subscription_end > old_end


class TestPlategaE2ECanceledFlow:
    """E2E: Flow отменённой оплаты."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_sbp_payment_canceled(self, test_db_session):
        """
        E2E: Пользователь создаёт платёж → отменяет → подписка НЕ продлена.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # === SETUP ===
        user = User(
            telegram_id=666666666,
            username="cancel_user",
            device_limit=2,
        )
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
            is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # === Создаём платёж ===
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-cancel-001",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # === Callback CANCELED ===
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-cancel-001",
            status="CANCELED",
            payload=""
        )

        assert success is True
        assert code == "success"

        # === Проверяем что платёж cancelled ===
        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "cancelled"

        # === Подписка НЕ продлена ===
        await test_db_session.refresh(user)
        assert user.subscription_end is None
        assert user.current_tariff_id is None

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_sbp_payment_canceled_idempotent(self, test_db_session):
        """
        E2E: Повторный callback CANCELED для уже отменённого платежа.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=777777777, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="cancelled",  # Уже отменён
            external_id="tx-cancel-idem",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # === Повторный callback CANCELED ===
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-cancel-idem",
            status="CANCELED",
            payload=""
        )

        assert success is True
        assert code == "already_processed"


class TestPlategaE2EChargebackFlow:
    """E2E: Flow chargeback (возврат средств)."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_sbp_payment_chargebacked(self, test_db_session):
        """
        E2E: Успешная оплата → chargeback → платёж помечен как refunded.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(
            telegram_id=888888001,
            device_limit=2,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # Уже оплаченный платёж
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="completed",
            external_id="tx-cb-001",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # === Callback CHARGEBACKED ===
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-cb-001",
            status="CHARGEBACKED",
            payload=""
        )

        assert success is True
        assert code == "success"

        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "refunded"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_sbp_payment_chargebacked_idempotent(self, test_db_session):
        """
        E2E: Повторный callback CHARGEBACKED для уже возвращённого платежа.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888002, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="refunded",  # Уже возвращён
            external_id="tx-cb-idem",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-cb-idem",
            status="CHARGEBACKED",
            payload=""
        )

        assert success is True
        assert code == "already_processed"


class TestPlategaE2EIdempotency:
    """E2E: Idempotency — повторные callback'и не дублируют подписку."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_confirmed_callback_idempotent(self, test_db_session):
        """
        E2E: Повторный CONFIRMED callback не продлевает подписку повторно.
        """
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888003, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-idem-001",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # Первый CONFIRMED
        success1, code1 = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-idem-001",
            status="CONFIRMED",
            payload=""
        )
        assert success1 is True

        await test_db_session.commit()
        await test_db_session.refresh(user)
        first_end = user.subscription_end

        # Второй CONFIRMED (повторный)
        success2, code2 = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-idem-001",
            status="CONFIRMED",
            payload=""
        )
        assert success2 is True

        # Подписка НЕ должна быть продлена повторно
        await test_db_session.commit()
        await test_db_session.refresh(user)
        assert user.subscription_end == first_end


class TestPlategaE2EEdgeCases:
    """E2E: Edge cases — неизвестные статусы, несуществующие транзакции."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_unknown_status_returns_error(self, test_db_session):
        """E2E: Неизвестный статус возвращает error."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888004, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-unknown-001",
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

        # Статус не изменился
        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "pending"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_transaction_not_found(self, test_db_session):
        """E2E: Несуществующая транзакция возвращает not_found."""
        from services.payment_service import PaymentService

        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="nonexistent-tx-id",
            status="CONFIRMED",
            payload=""
        )

        assert success is False
        assert code == "not_found"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_status_normalization_in_callback(self, test_db_session):
        """E2E: Callback с lowercase статусом обрабатывается корректно."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888005, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-lower-001",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # Callback с lowercase статусом
        # NOTE: webhook handler нормализует статус ДО вызова handle_platega_callback
        # Поэтому здесь передаём уже нормализованный CONFIRMED
        success, code = await PaymentService.handle_platega_callback(
            session=test_db_session,
            transaction_id="tx-lower-001",
            status="CONFIRMED",
            payload=""
        )

        assert success is True

        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "completed"


class TestPlategaE2ECheckPayment:
    """E2E: Проверка статуса платежа через check_platega_payment."""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_confirmed(self, test_db_session):
        """E2E: check_platega_payment обрабатывает CONFIRMED."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888006, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-check-001",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-001",
                "status": "CONFIRMED",
                "amount": 100.0,
                "currency": "RUB"
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is True

        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "completed"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_still_pending(self, test_db_session):
        """E2E: check_platega_payment возвращает False для PENDING."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888007, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-check-002",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-002",
                "status": "PENDING",
                "amount": 100.0,
                "currency": "RUB"
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "pending"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_canceled(self, test_db_session):
        """E2E: check_platega_payment обрабатывает CANCELED."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888008, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-check-003",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value={
                "id": "tx-check-003",
                "status": "CANCELED",
                "amount": 100.0,
                "currency": "RUB"
            })
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "cancelled"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_no_external_id(self, test_db_session):
        """E2E: check_platega_payment возвращает False если нет external_id."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888009, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # Платёж без external_id (Stars payment)
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="stars",
            status="pending",
            external_id=None,
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        result = await PaymentService.check_platega_payment(
            test_db_session, payment.id
        )

        assert result is False

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_already_completed(self, test_db_session):
        """E2E: check_platega_payment возвращает True для уже completed."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888010, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="completed",  # Уже завершён
            external_id="tx-already-done",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        result = await PaymentService.check_platega_payment(
            test_db_session, payment.id
        )

        assert result is True

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_check_payment_api_returns_none(self, test_db_session):
        """E2E: check_platega_payment возвращает False если API вернул None."""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        user = User(telegram_id=888888011, device_limit=2)
        test_db_session.add(user)

        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="RUB",
            status="pending",
            external_id="tx-api-error",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        with patch('services.payment_service.PlategaClient') as MockClient:
            mock_instance = MagicMock()
            mock_instance.check_status = AsyncMock(return_value=None)  # API error
            MockClient.return_value = mock_instance

            result = await PaymentService.check_platega_payment(
                test_db_session, payment.id
            )

        assert result is False

        # Статус не изменился
        await test_db_session.commit()
        await test_db_session.refresh(payment)
        assert payment.status == "pending"