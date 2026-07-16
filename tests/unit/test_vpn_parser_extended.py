# ... существующие тесты ...

    @pytest.mark.unit
    def test_is_valid_vpn_uri_rejects_awg_1(self):
        """🔥 НОВЫЙ ТЕСТ: AWG 1.0 (protocol_version = "1") должен быть rejected."""
        from utils.vpn_parser import is_valid_vpn_uri
        import base64, zlib, json

        # Конфиг AWG 1.0 (protocol_version = "1")
        awg1_config = {
            "containers": [{
                "container": "amneziawg",
                "awg": {
                    "protocol_version": "1",  # ← AWG 1.0
                    "port": "1234",
                    "J1": "10", "J2": "20", "J3": "30",  # AWG 1.0 параметры
                }
            }]
        }

        json_bytes = json.dumps(awg1_config).encode("utf-8")
        header = len(json_bytes).to_bytes(4, "big")
        compressed = zlib.compress(json_bytes)
        payload = base64.urlsafe_b64encode(header + compressed).decode("ascii").rstrip("=")
        uri = f"vpn://{payload}"

        # Должен вернуть False — AWG 1.0 не поддерживается
        assert is_valid_vpn_uri(uri) is False

    @pytest.mark.unit
    def test_is_valid_vpn_uri_rejects_awg_1_5(self):
        """🔥 НОВЫЙ ТЕСТ: AWG 1.5 (protocol_version = "1.5") должен быть rejected."""
        from utils.vpn_parser import is_valid_vpn_uri
        import base64, zlib, json

        awg15_config = {
            "containers": [{
                "container": "amneziawg",
                "awg": {
                    "protocol_version": "1.5",  # ← AWG 1.5
                    "port": "1234",
                }
            }]
        }

        json_bytes = json.dumps(awg15_config).encode("utf-8")
        header = len(json_bytes).to_bytes(4, "big")
        compressed = zlib.compress(json_bytes)
        payload = base64.urlsafe_b64encode(header + compressed).decode("ascii").rstrip("=")
        uri = f"vpn://{payload}"

        assert is_valid_vpn_uri(uri) is False

    @pytest.mark.unit
    def test_is_valid_vpn_uri_accepts_awg_2(self, sample_vpn_uri):
        """🔥 НОВЫЙ ТЕСТ: AWG 2.0 (protocol_version = "2") должен быть accepted."""
        from utils.vpn_parser import is_valid_vpn_uri
        # sample_vpn_uri из conftest.py имеет protocol_version = "2"
        assert is_valid_vpn_uri(sample_vpn_uri) is True

    @pytest.mark.unit
    def test_validate_awg2_config_rejects_protocol_version_1(self):
        """🔥 НОВЫЙ ТЕСТ: validate_awg2_config возвращает ERROR для protocol_version = "1"."""
        from utils.vpn_parser import validate_awg2_config

        data = {
            "containers": [{
                "awg": {
                    "protocol_version": "1",  # ← AWG 1.0
                    "S1": "79", "S2": "115", "S3": "5", "S4": "1",
                    "Jc": "4", "Jmax": "100",
                    "H1": "100-200", "H2": "300-400",
                    "H3": "500-600", "H4": "700-800",
                }
            }]
        }

        result = validate_awg2_config(data)
        assert result.is_valid is False
        assert any("protocol_version" in e and "AWG 1.0" in e for e in result.errors)

    @pytest.mark.unit
    def test_validate_awg2_config_accepts_protocol_version_2(self, sample_vpn_uri):
        """🔥 НОВЫЙ ТЕСТ: validate_awg2_config принимает protocol_version = "2"."""
        from utils.vpn_parser import decode_vpn_uri_to_json, validate_awg2_config

        data = decode_vpn_uri_to_json(sample_vpn_uri)
        result = validate_awg2_config(data)
        assert result.is_valid is True
        assert len(result.errors) == 0