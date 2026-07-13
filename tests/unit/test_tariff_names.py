"""Unit тесты для названий тарифов."""
import pytest


class TestTariffNames:
    @pytest.mark.unit
    def test_get_tariff_display_name_basic(self):
        from utils.tariff_names import get_tariff_display_name
        
        assert get_tariff_display_name(2) == "📱 Базовый"
    
    @pytest.mark.unit
    def test_get_tariff_display_name_family(self):
        from utils.tariff_names import get_tariff_display_name
        
        assert get_tariff_display_name(5) == "👨‍👩‍👧‍👦 Семейный"
    
    @pytest.mark.unit
    def test_get_tariff_display_name_pro(self):
        from utils.tariff_names import get_tariff_display_name
        
        assert get_tariff_display_name(10) == "🚀 Pro"
    
    @pytest.mark.unit
    def test_get_tariff_group_name(self):
        from utils.tariff_names import get_tariff_group_name
        
        assert "устр." in get_tariff_group_name(2)
        assert "2" in get_tariff_group_name(2)
