"""Integration тесты для payment handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestPaymentHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_payment_no_user(self, mock_bot):
        from bot.handlers.payment import hub_menu_payment

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await hub_menu_payment(callback, state, None, None)

        callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_payment_active_subscription(self, test_db_session, mock_bot):
        from bot.handlers.payment import hub_menu_payment
        from database.models import User

        user = User(
            telegram_id=222222222,
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 222222222
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 222222222
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.payment._show_hub') as mock_show:
            await hub_menu_payment(callback, state, user, test_db_session)

            mock_show.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_tariff_showcase_no_tariffs(self, test_db_session, mock_bot):
        from bot.handlers.payment import show_tariff_showcase_callback

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 333333333
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 333333333
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        with patch('bot.handlers.payment.get_active_tariffs', return_value=[]):
            with patch('bot.handlers.payment.render_hub') as mock_render:
                await show_tariff_showcase_callback(callback, test_db_session)

                mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_select_tariff_type_success(self, test_db_session, mock_bot):
        from bot.handlers.payment import select_tariff_type
        from database.models import Tariff

        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 444444444
        callback.data = "select_tariff_type:2"
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 444444444
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        with patch('bot.handlers.payment.render_hub') as mock_render:
            await select_tariff_type(callback, test_db_session)

            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_pay_stars_success(self, test_db_session, mock_bot):
        from bot.handlers.payment import pay_stars
        from database.models import User, Tariff

        user = User(telegram_id=555555555)
        test_db_session.add(user)
        await test_db_session.commit()

        tariff = Tariff(
            duration_days=30,
            device_limit=2,
            price_rub=100,
            price_stars=100,
        )
        test_db_session.add(tariff)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 555555555
        callback.data = f"pay_stars:{tariff.id}"
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 555555555
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.update_data = AsyncMock()

        with patch('bot.handlers.payment.create_payment', new_callable=AsyncMock) as mock_create:
            with patch('bot.handlers.payment.send_hub_invoice', new_callable=AsyncMock, return_value=123):
                mock_payment = MagicMock()
                mock_payment.id = 1
                mock_create.return_value = mock_payment

                await pay_stars(callback, state, user, test_db_session)

                callback.answer.assert_called()
                state.update_data.assert_called_once()
