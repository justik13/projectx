"""Расширенные unit тесты для VPN парсера."""
import pytest
import json


class TestVpnParserExtended:
    @pytest.mark.unit
    def test_parse_vpn_uri(self, sample_vpn_uri):
        from utils.vpn_parser import parse_vpn_uri
        
        config = parse_vpn_uri(sample_vpn_uri)
        assert config is not None
        assert config.protocol == "amneziawg2"
        assert config.protocol_version == "2"
        assert config.host_name == "test.server.com"
        assert config.address == "10.8.1.34/32"
        assert config.mtu == 1376

    @pytest.mark.unit
    def test_parse_vpn_uri_invalid(self):
        from utils.vpn_parser import parse_vpn_uri
        
        assert parse_vpn_uri("") is None
        assert parse_vpn_uri("invalid") is None
        assert parse_vpn_uri(None) is None

    @pytest.mark.unit
    def test_is_valid_amneziawg_config(self, sample_vpn_uri):
        from utils.vpn_parser import parse_vpn_uri, is_valid_amneziawg_config
        
        config = parse_vpn_uri(sample_vpn_uri)
        assert is_valid_amneziawg_config(config) is True
        
        assert is_valid_amneziawg_config(None) is False

    @pytest.mark.unit
    def test_amneziawg_config_dataclass(self):
        from utils.vpn_parser import AmneziaWGConfig
        
        config = AmneziaWGConfig()
        assert config.protocol == "amneziawg2"
        assert config.protocol_version == "2"
        assert config.peer_persistent_keepalive == 25
        assert config.peer_allowed_ips == "0.0.0.0/0, ::/0"

    @pytest.mark.unit
    def test_decode_base64url_invalid(self):
        from utils.vpn_parser import _decode_base64url
        
        # Невалидный base64
        result = _decode_base64url("!!!invalid!!!")
        assert result is None

    @pytest.mark.unit
    def test_decompress_invalid_header(self):
        from utils.vpn_parser import _decompress_amnezia_format
        
        # Слишком короткий для header
        result = _decompress_amnezia_format(b"\x00\x01")
        assert result is None

    @pytest.mark.unit
    def test_build_vpn_file_json_format(self, sample_vpn_uri):
        from utils.vpn_parser import build_vpn_file
        
        result = build_vpn_file(sample_vpn_uri)
        assert result is not None
        
        # Должен быть валидный JSON
        parsed = json.loads(result)
        assert "containers" in parsed
        assert "hostName" in parsed

    @pytest.mark.unit
    def test_build_conf_file_ini_format(self, sample_vpn_uri):
        from utils.vpn_parser import build_conf_file
        
        result = build_conf_file(sample_vpn_uri)
        assert result is not None
        
        # Должен быть WireGuard INI
        assert "[Interface]" in result
        assert "[Peer]" in result
        assert "PrivateKey" in result
        assert "PublicKey" in result

    @pytest.mark.unit
    def test_is_valid_vpn_uri_edge_cases(self):
        from utils.vpn_parser import is_valid_vpn_uri
        
        assert is_valid_vpn_uri("") is False
        assert is_valid_vpn_uri("vpn://") is False
        assert is_valid_vpn_uri("http://example.com") is False

    @pytest.mark.unit
    def test_h_value_parsing(self):
        from utils.vpn_parser import _parse_h_value
        
        assert _parse_h_value(None) == 0
        assert _parse_h_value(123) == 123
        assert _parse_h_value("123") == 123
        assert _parse_h_value("100-200") == "100-200"  # Диапазон как строка
        assert _parse_h_value("invalid") == "invalid"

    @pytest.mark.unit
    def test_int_value_parsing(self):
        from utils.vpn_parser import _parse_int_value
        
        assert _parse_int_value(None) == 0
        assert _parse_int_value(42) == 42
        assert _parse_int_value("42") == 42
        assert _parse_int_value("invalid") == 0
        assert _parse_int_value("invalid", default=99) == 99
