"""
Билдер валидного .conf файла для AmneziaWG / AmneziaWG 2.0.
Собирает конфиг из распарсенной структуры AmneziaWGConfig.

Поддерживает:
- AWG 1.0: J1, J2, J3, H1-H4 (числа), S1, S2
- AWG 2.0: Jc, Jmin, Jmax, H1-H4 (диапазоны), S1-S4, I1-I5
"""
import logging
from typing import Optional
from utils.vpn_parser import AmneziaWGConfig, is_valid_amneziawg_config

logger = logging.getLogger(__name__)


def _format_h_value(value) -> str:
    """Форматирует H1-H4 значение для .conf файла"""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value:
        return value
    return ""


def build_amneziawg_config(config: AmneziaWGConfig) -> Optional[str]:
    """
    Собирает валидный .conf файл для AmneziaWG / AmneziaWG 2.0.
    """
    if not is_valid_amneziawg_config(config):
        logger.warning("build_amneziawg_config: недостаточно данных")
        return None
    
    lines = []
    
    # ============ [Interface] ============
    lines.append("[Interface]")
    
    # Address
    if config.address:
        lines.append(f"Address = {config.address}")
    else:
        lines.append("Address = 10.8.0.2/32")
    
    # DNS
    if config.dns:
        lines.append(f"DNS = {config.dns}")
    else:
        lines.append("DNS = 1.1.1.1, 1.0.0.1")
    
    # MTU
    if config.mtu:
        lines.append(f"MTU = {config.mtu}")
    
    # PrivateKey
    lines.append(f"PrivateKey = {config.private_key}")
    
    # ============ Обфускационные параметры ============
    is_awg2 = config.protocol == "amneziawg2" or config.protocol_version == "2"
    
    if is_awg2:
        # Jc, Jmin, Jmax (AWG 2.0)
        if config.Jc:
            lines.append(f"Jc = {config.Jc}")
        if config.Jmin:
            lines.append(f"Jmin = {config.Jmin}")
        if config.Jmax:
            lines.append(f"Jmax = {config.Jmax}")
    else:
        # J1, J2, J3 (AWG 1.0)
        if config.J1:
            lines.append(f"J1 = {config.J1}")
        if config.J2:
            lines.append(f"J2 = {config.J2}")
        if config.J3:
            lines.append(f"J3 = {config.J3}")
    
    # S1-S4
    if config.S1:
        lines.append(f"S1 = {config.S1}")
    if config.S2:
        lines.append(f"S2 = {config.S2}")
    if config.S3:
        lines.append(f"S3 = {config.S3}")
    if config.S4:
        lines.append(f"S4 = {config.S4}")
    
    # H1-H4: могут быть числами (AWG 1.0) или диапазонами (AWG 2.0)
    h1 = _format_h_value(config.H1)
    h2 = _format_h_value(config.H2)
    h3 = _format_h_value(config.H3)
    h4 = _format_h_value(config.H4)
    if h1:
        lines.append(f"H1 = {h1}")
    if h2:
        lines.append(f"H2 = {h2}")
    if h3:
        lines.append(f"H3 = {h3}")
    if h4:
        lines.append(f"H4 = {h4}")
    
    # I1-I5 (AWG 2.0 only) — пакеты инициализации
    if is_awg2:
        if config.I1:
            lines.append(f"I1 = {config.I1}")
        if config.I2:
            lines.append(f"I2 = {config.I2}")
        if config.I3:
            lines.append(f"I3 = {config.I3}")
        if config.I4:
            lines.append(f"I4 = {config.I4}")
        if config.I5:
            lines.append(f"I5 = {config.I5}")
    
    # Пустая строка между секциями
    lines.append("")
    
    # ============ [Peer] ============
    lines.append("[Peer]")
    lines.append(f"PublicKey = {config.peer_public_key}")
    
    # PresharedKey
    if config.peer_preshared_key:
        lines.append(f"PresharedKey = {config.peer_preshared_key}")
    
    # AllowedIPs
    lines.append(f"AllowedIPs = {config.peer_allowed_ips or '0.0.0.0/0, ::/0'}")
    
    # Endpoint
    lines.append(f"Endpoint = {config.peer_endpoint}")
    
    # PersistentKeepalive
    lines.append(f"PersistentKeepalive = {config.peer_persistent_keepalive or 25}")
    
    return "\n".join(lines) + "\n"


def build_amneziawg_config_from_uri(vpn_uri: str) -> Optional[str]:
    """High-level: парсит vpn:// URI и сразу собирает .conf"""
    from utils.vpn_parser import parse_vpn_uri
    parsed = parse_vpn_uri(vpn_uri)
    if parsed is None:
        return None
    return build_amneziawg_config(parsed)