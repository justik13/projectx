"""Unit тесты для utils/telegram.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestTelegramUtils:
    @pytest.mark.unit
    def test_safe_none(self):
        from utils.telegram import safe
        assert safe(None) == "—"

    @pytest.mark.unit
    def test_safe_html_escape(self):
        from utils.telegram import safe
        assert safe("<script>") == "&lt;script&gt;"
        assert safe("Hello & World") == "Hello &amp; World"

    @pytest.mark.unit
    def test_safe_normal_string(self):
        from utils.telegram import safe
        assert safe("Hello World") == "Hello World"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_hub_cache(self):
        from utils.telegram import clear_hub_cache, _hub_cache
        
        _hub_cache[999999] = {"ids": [1, 2, 3]}
        clear_hub_cache(999999)
        assert 999999 not in _hub_cache

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_edit_text_success(self):
        from utils.telegram import safe_edit_text
        
        msg = MagicMock()
        msg.edit_text = AsyncMock()
        
        result = await safe_edit_text(msg, "new text")
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_edit_text_failure(self):
        from utils.telegram import safe_edit_text
        
        msg = MagicMock()
        msg.edit_text = AsyncMock(side_effect=Exception("Error"))
        
        result = await safe_edit_text(msg, "new text")
        assert result is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_delete_message(self):
        from utils.telegram import safe_delete_message
        
        msg = MagicMock()
        msg.delete = AsyncMock()
        
        result = await safe_delete_message(msg)
        assert result is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_answer_callback(self):
        from utils.telegram import safe_answer
        
        cb = MagicMock()
        cb.answer = AsyncMock()
        
        result = await safe_answer(cb, "text", show_alert=True)
        assert result is True
