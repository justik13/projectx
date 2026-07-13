"""Расширенные unit тесты для telegram utils."""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestTelegramUtilsExtended:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_hub_photo(self):
        from utils.telegram import send_hub_photo
        from aiogram.types import InputFile
        
        bot = MagicMock()
        bot.send_photo = AsyncMock(return_value=MagicMock(message_id=123))
        
        photo = MagicMock(spec=InputFile)
        
        msg_id = await send_hub_photo(bot, 111111, photo, "Caption")
        
        assert msg_id == 123
        bot.send_photo.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_hub_document(self):
        from utils.telegram import send_hub_document
        from aiogram.types import InputFile
        
        bot = MagicMock()
        bot.send_document = AsyncMock(return_value=MagicMock(message_id=456))
        
        document = MagicMock(spec=InputFile)
        
        msg_id = await send_hub_document(bot, 222222, document, "Caption")
        
        assert msg_id == 456
        bot.send_document.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_send_hub_invoice(self):
        from utils.telegram import send_hub_invoice
        
        bot = MagicMock()
        bot.send_invoice = AsyncMock(return_value=MagicMock(message_id=789))
        
        msg_id = await send_hub_invoice(
            bot, 333333,
            title="Test",
            description="Test",
            payload="test_payload",
            provider_token="",
            currency="XTR",
            prices=[]
        )
        
        assert msg_id == 789
        bot.send_invoice.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_append_hub_document(self):
        from utils.telegram import append_hub_document
        from aiogram.types import InputFile
        
        bot = MagicMock()
        bot.send_document = AsyncMock(return_value=MagicMock(message_id=111))
        
        document = MagicMock(spec=InputFile)
        
        msg_id = await append_hub_document(bot, 444444, document, "Caption")
        
        assert msg_id == 111
        bot.send_document.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_append_hub_message(self):
        from utils.telegram import append_hub_message
        
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=222))
        
        msg_id = await append_hub_message(bot, 555555, "Text")
        
        assert msg_id == 222
        bot.send_message.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_and_delete_hub(self):
        from utils.telegram import clear_and_delete_hub, _hub_cache
        
        bot = MagicMock()
        bot.delete_message = AsyncMock()
        
        _hub_cache[666666] = {"ids": [1, 2, 3]}
        
        await clear_and_delete_hub(bot, 666666)
        
        assert bot.delete_message.call_count == 3
        assert 666666 not in _hub_cache

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_render_hub_clears_old_messages(self):
        from utils.telegram import render_hub, _hub_cache
        from aiogram.types import InlineKeyboardMarkup
        
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        bot.delete_message = AsyncMock()
        
        _hub_cache[777777] = {"ids": [10, 20, 30]}
        
        kb = MagicMock(spec=InlineKeyboardMarkup)
        
        msg_id = await render_hub(bot, 777777, "New text", kb)
        
        assert msg_id == 999
        
        # 🔥 ИСПРАВЛЕНО: Даём event loop'у выполнить background task
        await asyncio.sleep(0)
        # Ещё раз для гарантии
        await asyncio.sleep(0)
        
        # Проверяем что старые сообщения были удалены
        assert bot.delete_message.call_count == 3
        
        # Проверяем что новое сообщение отправлено
        bot.send_message.assert_called_once()

    @pytest.mark.unit
    def test_safe_html_entities(self):
        from utils.telegram import safe
        
        assert safe("<script>") == "&lt;script&gt;"
        assert safe("Tom & Jerry") == "Tom &amp; Jerry"
        assert safe('He said "hello"') == "He said &quot;hello&quot;"
        assert safe("It's fine") == "It&#x27;s fine"

    @pytest.mark.unit
    def test_safe_special_characters(self):
        from utils.telegram import safe
        
        assert safe("Line1\nLine2") == "Line1\nLine2"
        assert safe("Tab\there") == "Tab\there"
        assert safe("Emoji 🚀") == "Emoji 🚀"

    @pytest.mark.unit
    def test_safe_numbers(self):
        from utils.telegram import safe
        
        assert safe(123) == "123"
        assert safe(45.67) == "45.67"
        assert safe(0) == "0"

    @pytest.mark.unit
    def test_safe_empty_values(self):
        from utils.telegram import safe
        
        assert safe("") == ""
        assert safe(None) == "—"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_edit_text_exception(self):
        from utils.telegram import safe_edit_text
        
        message = MagicMock()
        message.edit_text = AsyncMock(side_effect=Exception("Error"))
        
        result = await safe_edit_text(message, "text")
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_delete_message_exception(self):
        from utils.telegram import safe_delete_message
        
        message = MagicMock()
        message.delete = AsyncMock(side_effect=Exception("Error"))
        
        result = await safe_delete_message(message)
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_answer_exception(self):
        from utils.telegram import safe_answer
        
        callback = MagicMock()
        callback.answer = AsyncMock(side_effect=Exception("Error"))
        
        result = await safe_answer(callback, "text")
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_answer_no_text(self):
        from utils.telegram import safe_answer
        
        callback = MagicMock()
        callback.answer = AsyncMock()
        
        result = await safe_answer(callback)
        assert result is True
        callback.answer.assert_called_once_with(None, show_alert=False)
