"""Integration тесты для profile handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestProfileHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_profile_no_user(self, mock_bot):
        from bot.handlers.profile import hub_menu_profile

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await hub_menu_profile(callback, state, None, None)

        callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_profile_with_user(self, test_db_session, mock_bot):
        from bot.handlers.profile import hub_menu_profile
        from database.models import User

        user = User(
            telegram_id=222222222,
            username="testuser",
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 222222222
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.profile._render_profile') as mock_render:
            await hub_menu_profile(callback, state, user, test_db_session)

            state.clear.assert_called_once()
            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_history_empty(self, test_db_session, mock_bot):
        from bot.handlers.profile import show_history
        from database.models import User

        user = User(telegram_id=333333333)
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 333333333
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await show_history(callback, state, user, test_db_session)

        callback.message.edit_text.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_referral_success(self, test_db_session, mock_bot):
        from bot.handlers.profile import show_referral
        from database.models import User

        user = User(telegram_id=444444444, referral_days=5)
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 444444444
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.bot.get_me = AsyncMock(return_value=MagicMock(username="test_bot"))
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await show_referral(callback, state, user, test_db_session)

        callback.message.edit_text.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_referrals_list_empty(self, test_db_session, mock_bot):
        from bot.handlers.profile import show_referrals_list
        from database.models import User

        user = User(telegram_id=555555555)
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 555555555
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await show_referrals_list(callback, state, user, test_db_session)

        callback.message.edit_text.assert_called_once()
