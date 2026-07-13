"""Unit тесты для модуля форматирования."""
import pytest
from datetime import datetime, timezone, timedelta


class TestFormatters:
    @pytest.mark.unit
    def test_format_traffic_bytes(self):
        from utils.formatters import format_traffic
        
        assert format_traffic(0) == "0 B"
        assert format_traffic(100) == "100 B"
        assert format_traffic(1023) == "1023 B"
    
    @pytest.mark.unit
    def test_format_traffic_kilobytes(self):
        from utils.formatters import format_traffic
        
        assert format_traffic(1024) == "1.0 KB"
        assert format_traffic(1536) == "1.5 KB"
    
    @pytest.mark.unit
    def test_format_traffic_megabytes(self):
        from utils.formatters import format_traffic
        
        assert format_traffic(1048576) == "1.0 MB"
    
    @pytest.mark.unit
    def test_format_days_left_none(self):
        from utils.formatters import format_days_left
        assert format_days_left(None) == "—"
    
    @pytest.mark.unit
    def test_format_days_left_future(self):
        from utils.formatters import format_days_left
        
        future = datetime.now(timezone.utc) + timedelta(days=5, hours=3)
        result = format_days_left(future)
        assert "дн." in result
