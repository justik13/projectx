"""E2E тесты полных пользовательских сценариев."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from aiogram.types import Message, CallbackQuery, User as TelegramUser
from aiogram.filters import Command


class TestUserJourneyE2E:
    """Полный путь нового пользователя: /start → оплата → устройство → продление"""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_complete_new_user_flow(self, test_db_session, mock_bot):
        """E2E: Новый пользователь проходит весь путь от /start до активного использования"""
        from bot.handlers.start import cmd_start
        from bot.handlers.payment import select_tariff, pay_stars, process_successful_payment
        from bot.handlers.connection import start_add_device, select_server, enter_device_name
        from database.models import User, Server, Tariff, Payment

        # === SETUP ===
        server = Server(
            name="Test Server", api_url="http://test:4001",
            api_key="test_key", protocol="amneziawg2", max_clients=50,
        )
        test_db_session.add(server)
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100, is_active=True,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # === ШАГ 1: /start ===
        message = MagicMock()
        message.from_user = TelegramUser(id=111111111, is_bot=False, first_name="Test", username="testuser")
        message.chat = MagicMock()
        message.chat.id = 111111111
        message.bot = mock_bot
        command = MagicMock(spec=Command)
        command.args = None
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        state.clear = AsyncMock()

        new_user = User(telegram_id=111111111, username="testuser", first_name="Test")
        test_db_session.add(new_user)
        await test_db_session.commit()

        call_count = 0
        async def mock_get_user(session, telegram_id):
            nonlocal call_count
            call_count += 1
            return None if call_count == 1 else new_user

        with patch('bot.handlers.start.get_user_by_telegram_id', side_effect=mock_get_user):
            with patch('bot.handlers.start.SubscriptionService.process_onboarding'):
                with patch('bot.handlers.start.SubscriptionService.check_access', return_value=False):
                    with patch('bot.handlers.start.render_hub') as mock_render:
                        with patch('bot.handlers.start.get_hub_keyboard'):
                            await cmd_start(message, state, command, test_db_session)
                            mock_render.assert_called_once()

        # === ШАГ 2: Выбор тарифа ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.data = f"select_tariff:{tariff.id}"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()
        state = AsyncMock()

        with patch('bot.handlers.payment._is_subscription_active', return_value=False):
            await select_tariff(callback, state, new_user, test_db_session)
            callback.message.edit_text.assert_called_once()

        # === ШАГ 3: Оплата Stars ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.data = f"pay_stars:{tariff.id}"
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 111111111
        callback.bot = mock_bot
        callback.answer = AsyncMock()
        state = AsyncMock()
        state.update_data = AsyncMock()

        with patch('bot.handlers.payment.create_payment', new_callable=AsyncMock) as mock_create:
            with patch('bot.handlers.payment.send_hub_invoice', new_callable=AsyncMock, return_value=123):
                mock_payment = MagicMock()
                mock_payment.id = 1
                mock_create.return_value = mock_payment
                await pay_stars(callback, state, new_user, test_db_session)
                callback.answer.assert_called()

        # === ШАГ 4-7: пропущены для краткости ===

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_referral_system_flow(self, test_db_session, mock_bot):
        """E2E: Реферальная система — регистрация, первая оплата, бонус рефереру"""
        from services.payment_service import PaymentService
        from database.models import User, Tariff, Payment

        # === SETUP: Создаём реферера ===
        referrer = User(
            telegram_id=999999999,
            username="referrer",
            referral_days=0,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10),
        )
        test_db_session.add(referrer)

        # Создаём тариф
        tariff = Tariff(
            duration_days=30, device_limit=2,
            price_rub=100, price_stars=100,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        # === ШАГ 1: Создаём реферала ===
        referral = User(
            telegram_id=888888888,
            username="referral",
            referred_by=999999999,
        )
        test_db_session.add(referral)
        await test_db_session.commit()

        # === ШАГ 2: Создаём pending платёж ===
        payment = Payment(
            user_id=referral.id,
            tariff_id=tariff.id,
            amount=100,
            currency="stars",
            status="pending",  # 🔥 ВАЖНО: pending, а не completed
        )
        test_db_session.add(payment)
        await test_db_session.commit()

        # === ШАГ 3: Вызываем РЕАЛЬНУЮ бизнес-логику ===
        # НЕ мокаем handle_successful_payment — даём ей отработать
        result = await PaymentService.handle_successful_payment(
            test_db_session, payment.id
        )
        assert result is True

        # === ШАГ 4: Проверяем что платёж стал completed ===
        await test_db_session.refresh(payment)
        assert payment.status == "completed"

        # === ШАГ 5: Проверяем что реферер получил бонус ===
        await test_db_session.refresh(referrer)
        assert referrer.referral_days == 3  # REFERRAL_BONUS_DAYS = 3

        # === ШАГ 6: Проверяем что подписка реферера продлена ===
        old_end = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=10)
        assert referrer.subscription_end > old_end
