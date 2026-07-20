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
    """
    Разрешает http://localhost и http://127.0.0.1 только если это явно
    разрешено переменной окружения.

    По умолчанию разрешено, чтобы не ломать локальную разработку и текущие
    тестовые конфигурации, где Amnezia API может слушать http://127.0.0.1:4001.

    Для production рекомендуется:
    ALLOW_LOCAL_HTTP=false
    """
    raw = os.getenv("ALLOW_LOCAL_HTTP", "true")
    return _env_truthy(raw)


def _is_dangerous_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
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

    # IPv4-mapped IPv6 addresses can hide IPv4 private ranges.
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
    """
    Проверяет, что URL безопасен для использования в качестве внешнего API.

    Правила:
    1. Разрешены только http/https.
    2. http разрешён только для localhost/loopback и только если
       ALLOW_LOCAL_HTTP=true.
    3. https разрешён для публичных хостов.
    4. Запрещены metadata endpoint'ы и известные cloud metadata host'ы.
    5. Запрещены private, loopback, link-local, reserved, multicast IP
       для публичных https-хостов.
    6. DNS-резолвинг проверяется до фактического запроса.

    Важно:
    Эта проверка уменьшает риск SSRF, но для максимальной защиты в production
    рекомендуется дополнительно использовать allowlist доверенных хостов
    и запрет любых нежелательных сетей на уровне HTTP-клиента/прокси.
    """
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

        # Локальные адреса обрабатываем отдельно.
        if hostname in _LOCAL_HOSTNAMES:
            if scheme == "http":
                return _allow_local_http()

            # https для localhost разрешаем, это безопасно для локальной разработки.
            return True

        # Для http разрешаем только локальные адреса.
        if scheme == "http":
            return False

        # Если hostname уже является IP-адресом, проверяем его напрямую.
        try:
            ip = ipaddress.ip_address(hostname)
            return not _is_dangerous_ip(ip)
        except ValueError:
            pass

        # Для доменных имён проверяем все резолвящиеся IP-адреса.
        return await _resolved_ips_are_safe(hostname)

    except Exception:
        return False