import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "169.254.170.2",
}

_LOCAL_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
}


def _env_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _allow_local_http() -> bool:
    raw = os.getenv("ALLOW_LOCAL_HTTP", "true")
    return _env_truthy(raw)


def _is_dangerous_ip(ip) -> bool:
    if ip.is_private:
        return True
    if ip.is_loopback:
        return True
    if ip.is_link_local:
        return True
    if ip.is_reserved:
        return True
    if ip.is_multicast:
        return True
    if ip.is_unspecified:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return _is_dangerous_ip(ip.ipv4_mapped)
    return False


async def _resolved_ips_are_safe(hostname: str) -> bool:
    try:
        loop = asyncio.get_running_loop()
        addr_info = await asyncio.wait_for(
            loop.getaddrinfo(hostname, None),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        return False
    except socket.gaierror:
        return False
    except Exception:
        return False

    if not addr_info:
        return False

    for family, type_, proto, canonname, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if _is_dangerous_ip(ip):
            return False
    return True


async def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        scheme = (parsed.scheme or "").lower()

        if not hostname:
            return False

        if scheme not in {"http", "https"}:
            return False

        hostname = hostname.lower()

        if hostname in _BLOCKED_HOSTNAMES:
            return False

        if hostname in _LOCAL_HOSTNAMES:
            if scheme == "http":
                return _allow_local_http()
            return True

        if scheme == "http":
            return False

        try:
            ip = ipaddress.ip_address(hostname)
            return not _is_dangerous_ip(ip)
        except ValueError:
            pass

        return await _resolved_ips_are_safe(hostname)
    except Exception:
        return False