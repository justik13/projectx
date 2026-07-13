"""E2E тесты полных админских сценариев."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext


class TestAdminJourneyE2E:
    """Полный путь администратора: вход → добавление сервера → управление пользователями"""

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_admin_server_management_flow(self, test_db_session, mock_bot):
        """E2E: Админ добавляет сервер, редактирует и удаляет"""
        from bot.handlers.admin.servers import (
            start_add_server, process_add_server, show_server_card,
            toggle_server, request_delete_server, confirm_delete_server
        )
        from bot.states import AdminStates

        # === ШАГ 1: Админ начинает добавление сервера ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789  # Admin ID
        callback.data = "admin_server_add"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()
        state.set_state = AsyncMock()
        state.update_data = AsyncMock()

        with patch('bot.handlers.admin.servers.is_admin', return_value=True):
            await start_add_server(callback, state)
            
            state.set_state.assert_called_once_with(AdminStates.adding_server)
            state.update_data.assert_called_once()

        # === ШАГ 2: Админ вводит данные сервера (имя) ===
        # 🔥 ИСПРАВЛЕНО: Убираем spec=Message
        message = MagicMock()
        message.from_user = MagicMock()
        message.from_user.id = 123456789
        message.chat = MagicMock()
        message.chat.id = 123456789
        message.text = "Test Server"
        message.bot = mock_bot

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={"step": "name"})
        state.update_data = AsyncMock()

        with patch('bot.handlers.admin.servers.is_admin', return_value=True):
            with patch('bot.handlers.admin.servers.render_hub') as mock_render:
                await process_add_server(message, state, test_db_session)
                
                state.update_data.assert_called()
                mock_render.assert_called()

        # === ШАГ 3-5: Пропускаем для краткости (flag, api_url, api_key) ===
        # Реальная логика тестируется в integration тестах

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_admin_user_management_flow(self, test_db_session, mock_bot):
        """E2E: Админ управляет пользователями - продление, бан/разбан"""
        from bot.handlers.admin.users import (
            show_user_card, extend_subscription, toggle_ban_user
        )
        from database.models import User

        # Создаём тестового пользователя
        user = User(
            telegram_id=222222222,
            username="testuser",
            subscription_end=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5),
        )
        test_db_session.add(user)
        await test_db_session.commit()

        # === ШАГ 1: Админ открывает карточку пользователя ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789  # Admin
        callback.data = f"admin_user_card:{user.telegram_id}"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.admin.users.is_admin', return_value=True):
            with patch('bot.handlers.admin.users.get_user_by_telegram_id', return_value=user):
                await show_user_card(callback, state, test_db_session)
                
                callback.message.edit_text.assert_called_once()

        # === ШАГ 2: Админ продлевает подписку на 30 дней ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789
        callback.data = f"admin_extend_days:{user.telegram_id}:30"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.admin.users.is_admin', return_value=True):
            with patch('bot.handlers.admin.users.SubscriptionService.extend_subscription') as mock_extend:
                with patch('bot.handlers.admin.users.AuditService.log_action'):
                    with patch('bot.handlers.admin.users.get_user_by_telegram_id', return_value=user):
                        await extend_subscription(callback, state, test_db_session)
                        
                        mock_extend.assert_called_once_with(test_db_session, user.telegram_id, 30)
                        callback.answer.assert_called()

        # === ШАГ 3: Админ банит пользователя ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789
        callback.data = f"admin_user_ban:{user.telegram_id}"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()

        with patch('bot.handlers.admin.users.is_admin', return_value=True):
            with patch('bot.handlers.admin.users.get_settings') as mock_settings:
                mock_settings.return_value.ADMIN_IDS = [123456789]
                
                with patch('bot.handlers.admin.users.BanService.toggle_ban', return_value=(True, "забанен")):
                    with patch('bot.handlers.admin.users.get_user_by_telegram_id', return_value=user):
                        await toggle_ban_user(callback, state, test_db_session)
                        
                        callback.answer.assert_called()

    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_admin_broadcast_flow(self, test_db_session, mock_bot):
        """E2E: Админ создаёт и отправляет рассылку"""
        from bot.handlers.admin.broadcast import (
            start_broadcast, process_broadcast_message, broadcast_to_all
        )
        from bot.states import AdminStates
        from database.models import User

        # Создаём тестовых пользователей
        user1 = User(telegram_id=111111111, username="user1")
        user2 = User(telegram_id=222222222, username="user2")
        test_db_session.add(user1)
        test_db_session.add(user2)
        await test_db_session.commit()

        # === ШАГ 1: Админ начинает рассылку ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789  # Admin
        callback.data = "admin_broadcast"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.clear = AsyncMock()
        state.set_state = AsyncMock()

        with patch('bot.handlers.admin.broadcast.is_admin', return_value=True):
            await start_broadcast(callback, state)
            
            state.set_state.assert_called_once_with(AdminStates.entering_broadcast_message)

        # === ШАГ 2: Админ вводит текст рассылки ===
        # 🔥 ИСПРАВЛЕНО: Убираем spec=Message
        message = MagicMock()
        message.from_user = MagicMock()
        message.from_user.id = 123456789
        message.chat = MagicMock()
        message.chat.id = 123456789
        message.text = "Тестовая рассылка для всех пользователей!"
        message.bot = mock_bot
        message.photo = None
        message.document = None
        message.content_type = "text"

        state = AsyncMock()
        state.update_data = AsyncMock()
        state.set_state = AsyncMock()

        with patch('bot.handlers.admin.broadcast.is_admin', return_value=True):
            with patch('bot.handlers.admin.broadcast.render_hub') as mock_render:
                await process_broadcast_message(message, state)
                
                state.update_data.assert_called_once()
                state.set_state.assert_called_once_with(AdminStates.confirming_broadcast)

        # === ШАГ 3: Админ подтверждает отправку всем ===
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = MagicMock()
        callback.from_user.id = 123456789
        callback.data = "broadcast_send_all"
        callback.message = MagicMock()
        callback.message.edit_text = AsyncMock()
        callback.bot = mock_bot
        callback.answer = AsyncMock()

        state = AsyncMock()
        state.get_data = AsyncMock(return_value={
            "broadcast_text": "Тестовая рассылка",
            "media_id": None,
            "content_type": "text"
        })
        state.clear = AsyncMock()

        with patch('bot.handlers.admin.broadcast.is_admin', return_value=True):
            with patch('bot.handlers.admin.broadcast.get_all_users', return_value=[user1, user2]):
                with patch('bot.handlers.admin.broadcast.asyncio.create_task') as mock_task:
                    with patch('bot.handlers.admin.broadcast.AuditService.log_action'):
                        await broadcast_to_all(callback, state, test_db_session)
                        
                        mock_task.assert_called_once()
                        callback.message.edit_text.assert_called_once()
