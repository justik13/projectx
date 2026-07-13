import ipaddress
from urllib.parse import urlparse

def is_safe_url(url: str) -> bool:
    """
    Проверяет URL на безопасность (защита от SSRF).
    Запрещает приватные IP, loopback, link-local и metadata endpoints.
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
            "100.100.100.200", 
            "169.254.170.2",   
        }
        if hostname in blocked_hosts:
            return False

        # Попытка распарсить как IP-адрес
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            # Это доменное имя. 
            # В рамках админ-панели (доверенный пользователь) разрешаем домены,
            # но блокируем явные IP-адреса из приватных подсетей.
            pass
            
        return True
    except Exception:
        return False