"""Integration тесты для support handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock


class TestSupportHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_support(self, mock_bot):
        from bot.handlers.support import hub_menu_support

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        callback.bot = mock_bot

        state = AsyncMock()
        state.clear = AsyncMock()

        await hub_menu_support(callback, state)
        callback.message.edit_text.assert_called_once()
        callback.answer.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_faq(self, mock_bot):
        from bot.handlers.support import show_faq

        callback = MagicMock()
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await show_faq(callback)
        callback.message.edit_text.assert_called_once()
        call_args = callback.message.edit_text.call_args
        assert "Частые вопросы" in call_args[0][0]
        callback.answer.assert_called_once()
