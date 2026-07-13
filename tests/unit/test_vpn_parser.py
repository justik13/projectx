"""Unit тесты для парсера VPN URI."""
import pytest


class TestVpnParser:
    @pytest.mark.unit
    def test_decode_vpn_uri_valid(self, sample_vpn_uri):
        from utils.vpn_parser import decode_vpn_uri_to_json
        
        result = decode_vpn_uri_to_json(sample_vpn_uri)
        assert result is not None
        assert "containers" in result
    
    @pytest.mark.unit
    def test_build_vpn_file(self, sample_vpn_uri):
        from utils.vpn_parser import build_vpn_file
        import json
        
        result = build_vpn_file(sample_vpn_uri)
        assert result is not None
        parsed = json.loads(result)
        assert "containers" in parsed
    
    @pytest.mark.unit
    def test_build_conf_file(self, sample_vpn_uri):
        from utils.vpn_parser import build_conf_file
        
        result = build_conf_file(sample_vpn_uri)
        assert result is not None
        assert "[Interface]" in result
        assert "[Peer]" in result
    
    @pytest.mark.unit
    def test_is_valid_vpn_uri(self, sample_vpn_uri):
        from utils.vpn_parser import is_valid_vpn_uri
        
        assert is_valid_vpn_uri(sample_vpn_uri) is True
        assert is_valid_vpn_uri("invalid") is False
