# utils/config_builder.py
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
    
    Returns:
        Строка с содержимым .conf файла, или None если данных недостаточно.
    """
    if not is_valid_amneziawg_config(config):
        logger.warning("build_amneziawg_config: недостаточно данных для сборки")
        return None
    
    lines = []
    
    # ============ [Interface] ============
    lines.append("[Interface]")
    
    # Address
    if config.address:
        lines.append(f"Address = {config.address}")
    
    # DNS
    if config.dns:
        lines.append(f"DNS = {config.dns}")
    
    # PrivateKey
    lines.append(f"PrivateKey = {config.private_key}")
    
    # ============ Обфускационные параметры ============
    # Для AmneziaWG 2.0: Jc, Jmin, Jmax
    if config.protocol == "amneziawg2":
        if config.Jc:
            lines.append(f"Jc = {config.Jc}")
        if config.Jmin:
            lines.append(f"Jmin = {config.Jmin}")
        if config.Jmax:
            lines.append(f"Jmax = {config.Jmax}")
    else:
        # Для AmneziaWG v1: J1, J2, J3
        if config.J1:
            lines.append(f"J1 = {config.J1}")
        if config.J2:
            lines.append(f"J2 = {config.J2}")
        if config.J3:
            lines.append(f"J3 = {config.J3}")
    
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
    lines.append(f"AllowedIPs = {config.peer_allowed_ips}")
    
    # Endpoint
    lines.append(f"Endpoint = {config.peer_endpoint}")
    
    # PersistentKeepalive
    if config.peer_persistent_keepalive:
        lines.append(f"PersistentKeepalive = {config.peer_persistent_keepalive}")
    
    return "\n".join(lines) + "\n"


def build_amneziawg_config_from_uri(vpn_uri: str) -> Optional[str]:
    """
    High-level функция: парсит vpn:// URI и сразу собирает .conf.
    
    Returns:
        Строка с .conf или None при ошибке.
    """
    from utils.vpn_parser import parse_vpn_uri
    
    parsed = parse_vpn_uri(vpn_uri)
    if parsed is None:
        return None
    
    return build_amneziawg_config(parsed)