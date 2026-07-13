import ipaddress
import socket
from urllib.parse import urlparse

def is_safe_url(url: str) -> bool:
    """
    Проверяет URL на безопасность (защита от SSRF).
    - Разрешает localhost/127.0.0.1 для локального тестирования Amnezia API.
    - Блокирует приватные IP, link-local, metadata endpoints.
    - Блокирует домены, которые резолвятся в приватные IP-адреса.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        # Явный блок metadata endpoints (AWS, GCP, Alibaba и т.д.)
        blocked_hosts = {
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",  # Alibaba Cloud metadata
            "169.254.170.2",    # AWS ECS metadata
        }
        if hostname in blocked_hosts:
            return False

        # Разрешаем localhost для локального тестирования API
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True

        # Попытка распарсить как IP-адрес
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            # Это доменное имя. Резолвим его, чтобы проверить IP, в который он указывает.
            try:
                # getaddrinfo возвращает список кортежей с IP-адресами
                addr_info = socket.getaddrinfo(hostname, None)
                for family, type_, proto, canonname, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    ip = ipaddress.ip_address(ip_str)
                    # Если домен резолвится в приватный IP — блокируем
                    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                        return False
            except socket.gaierror:
                # Не удалось зарезолвить домен — пропускаем, aiohttp сам вернет ошибку подключения
                pass
            except Exception:
                return False
        
        return True
    except Exception:
        return False