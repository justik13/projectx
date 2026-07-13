"""Integration тесты для сервиса платежей."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch


class TestPaymentService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_successful_payment(self, test_db_session):
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # Создаём пользователя
        user = User(
            telegram_id=333333333,
            device_limit=2,
        )
        test_db_session.add(user)
        await test_db_session.commit()

        # Создаём тариф
        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
            is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # Создаём платёж
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="rub",
            status="pending",
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # Обрабатываем успешный платёж
        result = await PaymentService.handle_successful_payment(
            test_db_session, payment.id
        )

        assert result is True

        # Проверяем, что платёж помечен как completed
        await test_db_session.refresh(payment)
        assert payment.status == "completed"
        assert payment.paid_at is not None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_successful_payment_idempotent(self, test_db_session):
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # Создаём пользователя
        user = User(telegram_id=444444444)
        test_db_session.add(user)
        await test_db_session.commit()

        # Создаём тариф
        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
            is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # Создаём уже завершённый платёж
        payment = Payment(
            user_id=user.id,
            tariff_id=tariff.id,
            amount=100,
            currency="rub",
            status="completed",
            paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # Повторная обработка должна вернуть True (idempotent)
        result = await PaymentService.handle_successful_payment(
            test_db_session, payment.id
        )

        assert result is True
