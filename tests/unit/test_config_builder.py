"""Unit тесты для utils/config_builder.py"""
import pytest
import json


class TestConfigBuilder:
    @pytest.mark.unit
    def test_build_amneziawg_config_from_uri(self, sample_vpn_uri):
        from utils.config_builder import build_amneziawg_config_from_uri
        
        result = build_amneziawg_config_from_uri(sample_vpn_uri)
        assert result is not None
        assert len(result) > 0

    @pytest.mark.unit
    def test_build_amneziawg_config_from_invalid_uri(self):
        from utils.config_builder import build_amneziawg_config_from_uri
        
        result = build_amneziawg_config_from_uri("invalid_uri")
        assert result is None

    @pytest.mark.unit
    def test_build_amneziawg_config_none(self):
        from utils.config_builder import build_amneziawg_config
        
        result = build_amneziawg_config(None)
        assert result is None

    @pytest.mark.unit
    def test_build_amneziawg_config_with_raw_config(self, sample_vpn_uri):
        from utils.config_builder import build_amneziawg_config
        from utils.vpn_parser import parse_vpn_uri
        
        config = parse_vpn_uri(sample_vpn_uri)
        result = build_amneziawg_config(config)
        assert result is not None
