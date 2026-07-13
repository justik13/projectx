"""Integration тесты для middlewares."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, CallbackQuery


class TestCleanChatMiddleware:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_clean_chat_deletes_message(self):
        from bot.middlewares.clean_chat import CleanChatMiddleware

        middleware = CleanChatMiddleware()

        message = MagicMock()
        message.delete = AsyncMock()
        message.successful_payment = None
        message.pinned_message = None
        message.new_chat_members = None
        message.left_chat_member = None
        message.new_chat_title = None
        message.new_chat_photo = None
        message.delete_chat_photo = None
        message.group_chat_created = None
        message.supergroup_chat_created = None
        message.channel_chat_created = None
        message.migrate_to_chat_id = None
        message.migrate_from_chat_id = None

        handler = AsyncMock()
        await middleware(handler, message, {})
        handler.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_clean_chat_skips_successful_payment(self):
        from bot.middlewares.clean_chat import CleanChatMiddleware

        middleware = CleanChatMiddleware()

        message = MagicMock()
        message.successful_payment = MagicMock()
        message.delete = AsyncMock()
        handler = AsyncMock()
        await middleware(handler, message, {})
        handler.assert_called_once()


class TestDBSessionMiddleware:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_db_session_middleware(self):
        from bot.middlewares.db_session import DBSessionMiddleware

        middleware = DBSessionMiddleware()
        handler = AsyncMock()
        data = {}

        with patch('bot.middlewares.db_session.session_scope') as mock_scope:
            mock_scope.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_scope.return_value.__aexit__ = AsyncMock()
            await middleware(handler, MagicMock(), data)
            assert 'session' in data
            handler.assert_called_once()


class TestThrottlingMiddleware:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_throttling_first_call(self):
        from bot.middlewares.throttling import ThrottlingMiddleware

        middleware = ThrottlingMiddleware(limit=0.3)

        # 🔥 ИСПРАВЛЕНО: spec=CallbackQuery для isinstance()
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.data = "test_action"

        handler = AsyncMock()
        await middleware(handler, callback, {})
        handler.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_throttling_rapid_calls(self):
        from bot.middlewares.throttling import ThrottlingMiddleware

        middleware = ThrottlingMiddleware(limit=0.3)

        # 🔥 ИСПРАВЛЕНО: spec=CallbackQuery для isinstance()
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 222222222
        callback.data = "test_action"
        callback.answer = AsyncMock()

        handler = AsyncMock()
        await middleware(handler, callback, {})
        assert handler.call_count == 1

        await middleware(handler, callback, {})
        assert handler.call_count == 1  # Заблокировано
        callback.answer.assert_called()


class TestUserContextMiddleware:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_context_loads_user(self, test_db_session):
        from bot.middlewares.user_context import UserContextMiddleware
        from database.models import User

        user = User(telegram_id=333333333, username="testuser", first_name="Test")
        test_db_session.add(user)
        await test_db_session.commit()

        middleware = UserContextMiddleware()

        # 🔥 ИСПРАВЛЕНО: spec=Message для isinstance()
        message = MagicMock(spec=Message)
        message.from_user = MagicMock()
        message.from_user.id = 333333333

        handler = AsyncMock()
        data = {'session': test_db_session}
        await middleware(handler, message, data)
        assert 'db_user' in data
        handler.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_context_banned_user(self, test_db_session, mock_bot):
        from bot.middlewares.user_context import UserContextMiddleware
        from database.models import User

        user = User(telegram_id=444444444, username="banned", first_name="Banned", is_banned=True)
        test_db_session.add(user)
        await test_db_session.commit()

        middleware = UserContextMiddleware()

        # 🔥 ИСПРАВЛЕНО: spec=Message для isinstance()
        message = MagicMock(spec=Message)
        message.from_user = MagicMock()
        message.from_user.id = 444444444
        message.answer = AsyncMock()

        handler = AsyncMock()
        data = {'session': test_db_session}
        await middleware(handler, message, data)
        handler.assert_not_called()
        message.answer.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_context_no_session(self):
        from bot.middlewares.user_context import UserContextMiddleware

        middleware = UserContextMiddleware()

        message = MagicMock()
        message.from_user = MagicMock()
        message.from_user.id = 555555555

        handler = AsyncMock()
        data = {}
        await middleware(handler, message, data)
        handler.assert_called_once()
