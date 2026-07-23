import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from aiohttp.resolver import DefaultResolver

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


def _allow_local_http() -> bool:
    """
    Production-safe default:
    локальный HTTP запрещён, пока явно не включён.
    Для локальной разработки можно задать:
    ALLOW_LOCAL_HTTP=true
    """
    from config.settings import get_settings
    return get_settings().ALLOW_LOCAL_HTTP


def _allow_local_https() -> bool:
    from config.settings import get_settings
    return get_settings().ALLOW_LOCAL_HTTPS


def allow_local_networks() -> bool:
    """
    Используется в aiohttp resolver.
    Для production рекомендуется:
    ALLOW_LOCAL_HTTP=false
    ALLOW_LOCAL_HTTPS=false
    Тогда любые private/loopback/metadata адреса будут запрещены,
    включая DNS rebinding.
    """
    return _allow_local_http() or _allow_local_https()


def _host_is_localish(hostname: str) -> bool:
    """
    Возвращает True только если хост явно выглядит как локальный:
    - localhost;
    - 127.0.0.1;
    - ::1;
    - 0.0.0.0;
    - прямой private/loopback IP.
    Metadata/link-local адреса НЕ считаются разрешёнными локальными.
    """
    h = (hostname or "").lower().strip().strip("[]")
    if not h:
        return False
    if h in _LOCAL_HOSTNAMES:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_private or ip.is_unspecified


def is_ip_allowed(ip, *, allow_local: bool = False) -> bool:
    """
    Проверяет, разрешено ли подключаться к IP.
    Важно:
    - link-local заблокирован всегда, даже если allow_local=True;
    - reserved/multicast заблокированы всегда;
    - private/loopback/unspecified разрешены только если allow_local=True.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return False
    if ip.is_loopback or ip.is_private or ip.is_unspecified:
        return allow_local
    return True


def _is_dangerous_ip(ip) -> bool:
    return not is_ip_allowed(ip, allow_local=False)


class SafeResolver(DefaultResolver):
    """
    Resolver для aiohttp, который не даёт подключиться к опасным IP.
    Это закрывает DNS rebinding:
    даже если домен сначала резолвился в белый IP,
    но при фактическом подключении вернулся private/metadata IP,
    подключение будет заблокировано.
    """

    def __init__(self, *, allow_local: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.allow_local = allow_local

    async def resolve(self, host, port=0, family=socket.AF_INET):
        host_lower = (host or "").lower()
        if host_lower in _BLOCKED_HOSTNAMES:
            raise OSError(f"Blocked hostname: {host}")

        records = await super().resolve(host, port=port, family=family)

        # Разрешаем local IP только если:
        # 1) глобально разрешены локальные сети;
        # 2) хост явно выглядит локальным.
        #
        # Это защищает от DNS rebinding вида:
        # api.example.com -> 127.0.0.1
        allow_for_host = self.allow_local and _host_is_localish(host)

        safe_records = []
        for record in records:
            try:
                ip = ipaddress.ip_address(record["host"])
            except ValueError:
                continue
            if is_ip_allowed(ip, allow_local=allow_for_host):
                safe_records.append(record)

        if not safe_records:
            raise OSError(
                f"Unsafe or forbidden address resolved for host: {host}"
            )
        return safe_records


async def _resolved_ips_are_safe(
    hostname: str,
    *,
    allow_local: bool = False,
) -> bool:
    effective_allow_local = allow_local and _host_is_localish(hostname)
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
        if not is_ip_allowed(ip, allow_local=effective_allow_local):
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

        allow_local_https = _allow_local_https()

        if hostname in _LOCAL_HOSTNAMES:
            if scheme == "http":
                return _allow_local_http()
            if scheme == "https":
                return allow_local_https
            return False

        # Внешний HTTP запрещён.
        if scheme == "http":
            return False

        # Прямой IP-адрес.
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return is_ip_allowed(
                ip,
                allow_local=allow_local_https and _host_is_localish(hostname),
            )

        return await _resolved_ips_are_safe(
            hostname,
            allow_local=allow_local_https,
        )
    except Exception:
        return False