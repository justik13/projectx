"""Integration тесты для fallback handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestFallbackHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_media(self, mock_bot):
        from bot.handlers.fallback import handle_media
        from aiogram.types import Message

        # 🔥 ИСПРАВЛЕНО: Убираем spec=Message, настраиваем вручную
        message = MagicMock()
        message.chat = MagicMock()
        message.chat.id = 111111111
        message.bot = mock_bot

        with patch('bot.handlers.fallback.render_hub') as mock_render:
            await handle_media(message)
            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_unknown_text(self, mock_bot):
        from bot.handlers.fallback import handle_unknown_text
        from aiogram.types import Message

        message = MagicMock()
        message.text = "unknown command"
        message.chat = MagicMock()
        message.chat.id = 111111111
        message.bot = mock_bot

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.fallback.render_hub') as mock_render:
            await handle_unknown_text(message, state)
            state.clear.assert_called_once()
            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_unknown_text_empty(self, mock_bot):
        from bot.handlers.fallback import handle_unknown_text

        message = MagicMock()
        message.text = None
        message.bot = mock_bot
        state = AsyncMock()
        await handle_unknown_text(message, state)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_handle_unknown_text_command(self, mock_bot):
        from bot.handlers.fallback import handle_unknown_text

        message = MagicMock()
        message.text = "/start"
        message.bot = mock_bot
        state = AsyncMock()
        await handle_unknown_text(message, state)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_noop_group_header(self):
        from bot.handlers.fallback import noop_group_header
        from aiogram.types import CallbackQuery

        callback = MagicMock()
        callback.answer = AsyncMock()
        await noop_group_header(callback)
        callback.answer.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_dismiss_notification(self):
        from bot.handlers.fallback import dismiss_notification

        callback = MagicMock()
        callback.answer = AsyncMock()
        callback.message = MagicMock()
        callback.message.delete = AsyncMock()
        await dismiss_notification(callback)
        callback.answer.assert_called_once()
        callback.message.delete.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_fsm_media_guard(self, mock_bot):
        from bot.handlers.fallback import fsm_media_guard

        message = MagicMock()
        message.chat = MagicMock()
        message.chat.id = 111111111
        message.bot = mock_bot

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.fallback.render_hub') as mock_render:
            await fsm_media_guard(message, state)
            state.clear.assert_called_once()
            mock_render.assert_called_once()
