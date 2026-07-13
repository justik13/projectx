"""Integration тесты для connection handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from aiogram.types import CallbackQuery, Message, BufferedInputFile


class TestConnectionHandler:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_connections_no_user(self, mock_bot):
        from bot.handlers.connection import hub_menu_connections

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 111111111
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 111111111
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        session = AsyncMock()

        await hub_menu_connections(callback, state, session, None)

        callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_hub_menu_connections_with_user(self, test_db_session, mock_bot):
        from bot.handlers.connection import hub_menu_connections
        from database.models import User

        user = User(
            telegram_id=222222222,
            username="testuser",
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 222222222
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 222222222
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.connection._render_connections') as mock_render:
            await hub_menu_connections(callback, state, test_db_session, user)

            state.clear.assert_called_once()
            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_manage_device_invalid_profile(self, test_db_session, mock_bot):
        from bot.handlers.connection import manage_device

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 333333333
        callback.data = "manage_device:999"
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        await manage_device(callback, state, test_db_session, None)

        callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_show_config_success(self, test_db_session, mock_bot):
        from bot.handlers.connection import show_config
        from database.models import User, Server, VPNProfile

        user = User(telegram_id=444444444)
        test_db_session.add(user)
        await test_db_session.commit()

        server = Server(
            name="Test",
            api_url="http://test:4001",
            api_key="key",
            protocol="amneziawg2",
        )
        test_db_session.add(server)
        await test_db_session.commit()

        profile = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name="Device",
            peer_id="peer",
            raw_config="vpn://test",
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 444444444
        callback.data = f"show_config:{profile.id}"
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 444444444
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.connection.render_hub') as mock_render:
            await show_config(callback, state, test_db_session, user)

            mock_render.assert_called_once()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_download_conf_success(self, test_db_session, mock_bot):
        from bot.handlers.connection import download_conf
        from database.models import User, Server, VPNProfile

        user = User(telegram_id=555555555)
        test_db_session.add(user)
        await test_db_session.commit()

        server = Server(
            name="Test",
            api_url="http://test:4001",
            api_key="key",
            protocol="amneziawg2",
        )
        test_db_session.add(server)
        await test_db_session.commit()

        profile = VPNProfile(
            user_id=user.id,
            server_id=server.id,
            device_name="Device",
            peer_id="peer",
            raw_config="vpn://test",
        )
        test_db_session.add(profile)
        await test_db_session.commit()

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 555555555
        callback.data = f"download_conf:{profile.id}"
        callback.message = MagicMock()
        callback.message.chat = MagicMock()
        callback.message.chat.id = 555555555
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.connection.build_vpn_file', return_value="vpn_content"):
            with patch('bot.handlers.connection.build_conf_file', return_value="conf_content"):
                with patch('bot.handlers.connection.clear_and_delete_hub', new_callable=AsyncMock):
                    with patch('bot.handlers.connection.append_hub_document', new_callable=AsyncMock):
                        with patch('bot.handlers.connection.append_hub_message', new_callable=AsyncMock):
                            await download_conf(callback, state, test_db_session, user)

                            callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_add_device_no_servers(self, test_db_session, mock_bot):
        from bot.handlers.connection import start_add_device

        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 666666666
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.connection.get_available_servers', return_value=[]):
            await start_add_device(callback, state, test_db_session)

            callback.answer.assert_called()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_enter_device_name_invalid(self, test_db_session, mock_bot):
        from bot.handlers.connection import enter_device_name
        from database.models import User

        user = User(telegram_id=777777777)
        test_db_session.add(user)
        await test_db_session.commit()

        # 🔥 ИСПРАВЛЕНО: Убираем spec=Message, настраиваем вручную
        message = MagicMock()
        message.from_user = MagicMock()
        message.from_user.id = 777777777
        message.chat = MagicMock()
        message.chat.id = 777777777
        message.text = "invalid!@#"
        message.bot = mock_bot

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.connection.render_hub') as mock_render:
            await enter_device_name(message, state, test_db_session, user)

            mock_render.assert_called_once()
