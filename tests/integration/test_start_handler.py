"""Integration тесты для start handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from aiogram.types import User as TelegramUser, Message
from aiogram.filters import Command


class TestStartHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cmd_start_new_user(self, test_db_session, mock_bot):
        from bot.handlers.start import cmd_start
        from database.models import User

        # Создаём пользователя который будет возвращён после process_onboarding
        new_user = User(
            telegram_id=111111111,
            username="testuser",
            first_name="Test",
            subscription_end=None,
        )
        test_db_session.add(new_user)
        await test_db_session.commit()

        message = MagicMock(spec=Message)
        message.from_user = TelegramUser(
            id=111111111, is_bot=False, first_name="Test", username="testuser"
        )
        message.chat = MagicMock()
        message.chat.id = 111111111
        message.bot = mock_bot

        command = MagicMock(spec=Command)
        command.args = None

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        state.clear = AsyncMock()

        # 🔥 ИСПРАВЛЕНО: side_effect - первый вызов None, второй - пользователь
        call_count = 0
        async def mock_get_user(session, telegram_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # Первый вызов: пользователь не найден
            return new_user  # Второй вызов: после onboarding

        with patch('bot.handlers.start.get_user_by_telegram_id', side_effect=mock_get_user):
            with patch('bot.handlers.start.SubscriptionService.process_onboarding') as mock_onboarding:
                with patch('bot.handlers.start.SubscriptionService.check_access', return_value=False):
                    with patch('bot.handlers.start.render_hub') as mock_render:
                        with patch('bot.handlers.start.get_hub_keyboard'):
                            await cmd_start(message, state, command, test_db_session)
                            mock_onboarding.assert_called_once()
                            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cmd_start_existing_user(self, test_db_session, mock_bot):
        from bot.handlers.start import cmd_start
        from database.models import User

        user = User(
            telegram_id=222222222, username="existing", first_name="Existing",
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        message = MagicMock(spec=Message)
        message.from_user = TelegramUser(
            id=222222222, is_bot=False, first_name="Existing", username="existing"
        )
        message.chat = MagicMock()
        message.chat.id = 222222222
        message.bot = mock_bot

        command = MagicMock(spec=Command)
        command.args = None
        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        state.clear = AsyncMock()

        with patch('bot.handlers.start.SubscriptionService.check_access', return_value=True):
            with patch('bot.handlers.start.render_hub') as mock_render:
                with patch('bot.handlers.start.get_hub_keyboard'):
                    await cmd_start(message, state, command, test_db_session)
                    mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_cmd_start_with_referral(self, test_db_session, mock_bot):
        from bot.handlers.start import cmd_start
        from database.models import User

        # Создаём пользователя который будет возвращён после process_onboarding
        new_user = User(
            telegram_id=333333333,
            username="referred",
            first_name="Referred",
            referred_by=999999999,
            subscription_end=None,
        )
        test_db_session.add(new_user)
        await test_db_session.commit()

        message = MagicMock(spec=Message)
        message.from_user = TelegramUser(
            id=333333333, is_bot=False, first_name="Referred", username="referred"
        )
        message.chat = MagicMock()
        message.chat.id = 333333333
        message.bot = mock_bot

        command = MagicMock(spec=Command)
        command.args = "ref_999999999"

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        state.clear = AsyncMock()

        # 🔥 ИСПРАВЛЕНО: side_effect
        call_count = 0
        async def mock_get_user(session, telegram_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return new_user

        with patch('bot.handlers.start.get_user_by_telegram_id', side_effect=mock_get_user):
            with patch('bot.handlers.start.SubscriptionService.process_onboarding') as mock_onboarding:
                with patch('bot.handlers.start.SubscriptionService.check_access', return_value=False):
                    with patch('bot.handlers.start.render_hub'):
                        with patch('bot.handlers.start.get_hub_keyboard'):
                            await cmd_start(message, state, command, test_db_session)
                            call_args = mock_onboarding.call_args
                            assert call_args[0][4] == 999999999

    @pytest.mark.unit
    def test_parse_referral_id_valid(self):
        from bot.handlers.start import parse_referral_id
        assert parse_referral_id("ref_123456789") == 123456789

    @pytest.mark.unit
    def test_parse_referral_id_invalid(self):
        from bot.handlers.start import parse_referral_id
        assert parse_referral_id("") is None
        assert parse_referral_id(None) is None
        assert parse_referral_id("invalid") is None
        assert parse_referral_id("ref_abc") is None
