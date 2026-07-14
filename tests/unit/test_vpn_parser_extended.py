"""Расширенные unit тесты для VPN парсера."""
import pytest
import json


class TestVpnParserExtended:

    @pytest.mark.unit
    def test_decode_base64url_invalid(self):
        from utils.vpn_parser import _decode_base64url
        result = _decode_base64url("!!!invalid!!!")
        assert result is None

    @pytest.mark.unit
    def test_decompress_invalid_header(self):
        from utils.vpn_parser import _decompress_amnezia_format
        result = _decompress_amnezia_format(b"\x00\x01")
        assert result is None

    @pytest.mark.unit
    def test_build_vpn_file_json_format(self, sample_vpn_uri):
        from utils.vpn_parser import build_vpn_file
        result = build_vpn_file(sample_vpn_uri)
        assert result is not None
        parsed = json.loads(result)
        assert "containers" in parsed
        assert "hostName" in parsed

    @pytest.mark.unit
    def test_build_conf_file_ini_format(self, sample_vpn_uri):
        from utils.vpn_parser import build_conf_file
        result = build_conf_file(sample_vpn_uri)
        assert result is not None
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
    def test_validate_awg2_config_valid(self, sample_vpn_uri):
        from utils.vpn_parser import decode_vpn_uri_to_json, validate_awg2_config
        data = decode_vpn_uri_to_json(sample_vpn_uri)
        result = validate_awg2_config(data)
        assert result.is_valid is True
        assert len(result.errors) == 0

    @pytest.mark.unit
    def test_validate_awg2_config_invalid_data(self):
        from utils.vpn_parser import validate_awg2_config
        result = validate_awg2_config("not a dict")
        assert result.is_valid is False
        assert "Data is not a dictionary" in result.errors

    @pytest.mark.unit
    def test_validate_awg2_config_missing_containers(self):
        from utils.vpn_parser import validate_awg2_config
        result = validate_awg2_config({})
        assert result.is_valid is False
        assert "Missing 'containers' array" in result.errors

    @pytest.mark.unit
    def test_validate_awg2_config_s4_too_large(self):
        from utils.vpn_parser import validate_awg2_config
        data = {
            "containers": [{
                "awg": {
                    "protocol_version": "2",
                    "S1": "79", "S2": "115", "S3": "5", "S4": "99",
                    "Jc": "4", "Jmax": "100",
                    "H1": "100-200", "H2": "300-400",
                    "H3": "500-600", "H4": "700-800",
                }
            }]
        }
        result = validate_awg2_config(data)
        assert result.is_valid is False
        assert any("S4" in e for e in result.errors)