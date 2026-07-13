"""Unit тесты для модуля безопасности."""
import pytest


class TestSecurity:
    @pytest.mark.unit
    def test_is_safe_url_localhost(self):
        from utils.security import is_safe_url
        
        assert is_safe_url("http://localhost:8080") is True
        assert is_safe_url("http://127.0.0.1:4001") is True
    
    @pytest.mark.unit
    def test_is_safe_url_private_ip(self):
        from utils.security import is_safe_url
        
        assert is_safe_url("http://192.168.1.1") is False
        assert is_safe_url("http://10.0.0.1") is False
    
    @pytest.mark.unit
    def test_is_safe_url_metadata(self):
        from utils.security import is_safe_url
        
        assert is_safe_url("http://169.254.169.254") is False
        assert is_safe_url("http://metadata.google.internal") is False
