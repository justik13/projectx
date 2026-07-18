import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

async def is_safe_url(url: str) -> bool:
    """
    Асинхронная проверка URL на безопасность (защита от SSRF).
    🔥 ИСПРАВЛЕНО: Жесткое требование HTTPS для внешних сетей.
    Так как бот и API на разных VPS, HTTP = MITM уязвимость.
    HTTP разрешен ТОЛЬКО для localhost/127.0.0.1 (для локальных тестов).
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        scheme = parsed.scheme.lower()
        
        if not hostname:
            return False
            
        # 🔥 КРИТИЧЕСКИЙ ФИКС: Бот и API на разных VPS.
        # Трафик идет через публичный интернет. HTTP = MITM уязвимость.
        if scheme == "http":
            if hostname not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                return False
        elif scheme != "https":
            return False

        # Блокируем metadata endpoints
        blocked_hosts = {
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",
            "169.254.170.2",
        }
        if hostname in blocked_hosts:
            return False

        # Разрешаем localhost для локального API
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True

        # Проверяем IP-адрес
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            # Доменное имя - резолвим асинхронно с таймаутом
            try:
                loop = asyncio.get_running_loop()
                # 🔥 MUST FIX #5: Timeout 5 секунд на DNS resolution
                addr_info = await asyncio.wait_for(
                    loop.getaddrinfo(hostname, None),
                    timeout=5.0
                )
                for family, type_, proto, canonname, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                        return False
            except asyncio.TimeoutError:
                # DNS resolution занял > 5 секунд — считаем небезопасным
                return False
            except socket.gaierror:
                # Домен не резолвится — пропускаем (возможно, внутренний DNS)
                pass
        except Exception:
            return False
        return True
    except Exception:
        return False