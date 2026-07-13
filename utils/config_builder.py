"""
Билдер валидного .conf файла для AmneziaWG / AmneziaWG 2.0.
Собирает конфиг из распарсенной структуры AmneziaWGConfig.
"""
import logging
from typing import Optional
from utils.vpn_parser import AmneziaWGConfig, is_valid_amneziawg_config

logger = logging.getLogger(__name__)


def build_amneziawg_config(config: AmneziaWGConfig) -> Optional[str]:
    """
    Собирает валидный .conf файл для AmneziaWG / AmneziaWG 2.0.
    Генерирует даже если обфускационные параметры отсутствуют — использует дефолты.
    """
    if not is_valid_amneziawg_config(config):
        logger.warning("build_amneziawg_config: недостаточно данных для сборки")
        return None
    
    lines = []
    
    # ============ [Interface] ============
    lines.append("[Interface]")
    
    # Address (дефолт 10.8.0.x/32)
    if config.address:
        lines.append(f"Address = {config.address}")
    else:
        lines.append("Address = 10.8.0.2/32")
    
    # DNS
    if config.dns:
        lines.append(f"DNS = {config.dns}")
    else:
        lines.append("DNS = 1.1.1.1, 8.8.8.8")
    
    # PrivateKey (обязательно!)
    lines.append(f"PrivateKey = {config.private_key}")
    
    # ============ Обфускационные параметры ============
    if config.protocol == "amneziawg2":
        # AmneziaWG 2.0: Jc, Jmin, Jmax
        lines.append(f"Jc = {config.Jc or 5}")
        lines.append(f"Jmin = {config.Jmin or 50}")
        lines.append(f"Jmax = {config.Jmax or 100}")
    else:
        # AmneziaWG v1: J1, J2, J3
        lines.append(f"J1 = {config.J1 or 40}")
        lines.append(f"J2 = {config.J2 or 80}")
        lines.append(f"J3 = {config.J3 or 120}")
    
    # Общие параметры: S1, S2, H1-H4
    if config.S1:
        lines.append(f"S1 = {config.S1}")
    if config.S2:
        lines.append(f"S2 = {config.S2}")
    if config.H1:
        lines.append(f"H1 = {config.H1}")
    if config.H2:
        lines.append(f"H2 = {config.H2}")
    if config.H3:
        lines.append(f"H3 = {config.H3}")
    if config.H4:
        lines.append(f"H4 = {config.H4}")
    
    # Пустая строка между секциями
    lines.append("")
    
    # ============ [Peer] ============
    lines.append("[Peer]")
    lines.append(f"PublicKey = {config.peer_public_key}")
    
    # PresharedKey (опционально)
    if config.peer_preshared_key:
        lines.append(f"PresharedKey = {config.peer_preshared_key}")
    
    # AllowedIPs
    lines.append(f"AllowedIPs = {config.peer_allowed_ips or '0.0.0.0/0, ::/0'}")
    
    # Endpoint (обязательно!)
    lines.append(f"Endpoint = {config.peer_endpoint}")
    
    # PersistentKeepalive
    lines.append(f"PersistentKeepalive = {config.peer_persistent_keepalive or 25}")
    
    return "\n".join(lines) + "\n"


def build_amneziawg_config_from_uri(vpn_uri: str) -> Optional[str]:
    """
    High-level функция: парсит vpn:// URI и сразу собирает .conf.
    """
    from utils.vpn_parser import parse_vpn_uri
    parsed = parse_vpn_uri(vpn_uri)
    if parsed is None:
        return None
    return build_amneziawg_config(parsed)