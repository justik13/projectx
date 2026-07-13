"""Расширенные unit тесты для форматтеров."""
import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


class TestFormattersExtended:
    @pytest.mark.unit
    def test_format_traffic_gigabytes(self):
        from utils.formatters import format_traffic
        
        assert format_traffic(1073741824) == "1.0 GB"  # 1 GB
        assert format_traffic(1610612736) == "1.5 GB"  # 1.5 GB

    @pytest.mark.unit
    def test_format_traffic_terabytes(self):
        from utils.formatters import format_traffic
        
        assert format_traffic(1099511627776) == "1.0 TB"  # 1 TB

    @pytest.mark.unit
    def test_to_msk_none(self):
        from utils.formatters import to_msk
        
        assert to_msk(None) is None

    @pytest.mark.unit
    def test_to_msk_naive(self):
        from utils.formatters import to_msk
        
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        result = to_msk(naive_dt)
        
        assert result is not None
        assert result.tzinfo is not None

    @pytest.mark.unit
    def test_to_msk_aware(self):
        from utils.formatters import to_msk
        
        aware_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = to_msk(aware_dt)
        
        assert result is not None
        assert result.tzinfo is not None

    @pytest.mark.unit
    def test_format_datetime_none(self):
        from utils.formatters import format_datetime
        
        assert format_datetime(None) == "—"

    @pytest.mark.unit
    def test_format_datetime_valid(self):
        from utils.formatters import format_datetime
        
        dt = datetime(2026, 7, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = format_datetime(dt)
        
        assert "15" in result
        assert "07" in result
        assert "2026" in result

    @pytest.mark.unit
    def test_format_days_left_past(self):
        from utils.formatters import format_days_left
        
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=5)
        result = format_days_left(past)
        
        assert result == "—"

    @pytest.mark.unit
    def test_format_days_left_hours_only(self):
        from utils.formatters import format_days_left
        
        # Менее 24 часов
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
        result = format_days_left(future)
        
        assert "ч." in result
        assert "дн." not in result

    @pytest.mark.unit
    def test_format_datetime_short(self):
        from utils.formatters import format_datetime_short
        
        assert format_datetime_short(None) == "—"
        
        dt = datetime(2026, 7, 15, 14, 30, 0, tzinfo=timezone.utc)
        result = format_datetime_short(dt)
        assert "15.07" in result

    @pytest.mark.unit
    def test_format_traffic_boundary_values(self):
        from utils.formatters import format_traffic
        
        # Границы единиц измерения
        assert format_traffic(1023) == "1023 B"
        assert format_traffic(1024) == "1.0 KB"
        assert format_traffic(1048575) == "1024.0 KB"  # Почти 1 MB
        assert format_traffic(1048576) == "1.0 MB"
