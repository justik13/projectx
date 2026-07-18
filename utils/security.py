import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

async def is_safe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        scheme = parsed.scheme.lower()
        
        if not hostname:
            return False
        if scheme == "http":
            if hostname not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
                return False
        elif scheme != "https":
            return False
        blocked_hosts = {
            "169.254.169.254",
            "metadata.google.internal",
            "100.100.100.200",
            "169.254.170.2",
        }
        if hostname in blocked_hosts:
            return False
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        except ValueError:
            try:
                loop = asyncio.get_running_loop()
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
                return False
            except socket.gaierror:
                pass
        except Exception:
            return False
        return True
    except Exception:
        return False