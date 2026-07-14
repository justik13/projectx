"""Расширенные unit тесты для security utils."""
import pytest
from unittest.mock import patch, MagicMock


class TestSecurityExtended:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_valid_http(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://example.com") is True
        assert await is_safe_url("https://example.com") is True
        assert await is_safe_url("http://example.com:8080") is True
        assert await is_safe_url("https://api.example.com/v1/endpoint") is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_ftp_allowed(self):
        """FTP с публичным hostname разрешён (функция не проверяет scheme)"""
        from utils.security import is_safe_url
        assert await is_safe_url("ftp://example.com") is True
        assert await is_safe_url("ftp://ftp.example.org") is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_no_hostname_blocked(self):
        """URLs без hostname блокируются (это правильное поведение безопасности)"""
        from utils.security import is_safe_url
        assert await is_safe_url("file:///etc/passwd") is False
        assert await is_safe_url("javascript:alert(1)") is False
        assert await is_safe_url("data:text/html,<h1>hi</h1>") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_empty_hostname(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://") is False
        assert await is_safe_url("https:///path") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_private_ip_ranges(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://10.0.0.1") is False
        assert await is_safe_url("http://172.16.0.1") is False
        assert await is_safe_url("http://172.31.255.255") is False
        assert await is_safe_url("http://192.168.1.1") is False
        assert await is_safe_url("http://169.254.0.1") is False
        assert await is_safe_url("http://127.0.0.2") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_metadata_endpoints(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://169.254.169.254") is False
        assert await is_safe_url("http://169.254.169.254/latest/meta-data/") is False
        assert await is_safe_url("http://metadata.google.internal") is False
        assert await is_safe_url("http://100.100.100.200") is False
        assert await is_safe_url("http://169.254.170.2") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_localhost_allowed(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://localhost") is True
        assert await is_safe_url("http://localhost:8080") is True
        assert await is_safe_url("http://127.0.0.1") is True
        assert await is_safe_url("http://127.0.0.1:4001") is True
        assert await is_safe_url("http://[::1]") is True
        assert await is_safe_url("http://0.0.0.0") is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_domain_resolves_to_private(self):
        from utils.security import is_safe_url
        with patch('utils.security.socket.getaddrinfo') as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('192.168.1.100', 0))
            ]
            assert await is_safe_url("http://internal.company.local") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_domain_resolves_to_public(self):
        from utils.security import is_safe_url
        with patch('utils.security.socket.getaddrinfo') as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('8.8.8.8', 0))
            ]
            assert await is_safe_url("http://google.com") is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_dns_resolution_fails(self):
        from utils.security import is_safe_url
        import socket
        with patch('utils.security.socket.getaddrinfo', side_effect=socket.gaierror):
            assert await is_safe_url("http://nonexistent.domain.xyz") is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_ipv6_addresses(self):
        from utils.security import is_safe_url
        assert await is_safe_url("http://[2001:4860:4860::8888]") is True
        assert await is_safe_url("http://[::1]") is True
        assert await is_safe_url("http://[fe80::1]") is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_is_safe_url_exception_handling(self):
        from utils.security import is_safe_url
        assert await is_safe_url("") is False
        assert await is_safe_url("not a url") is False
        assert await is_safe_url("http://") is False