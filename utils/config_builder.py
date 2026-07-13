"""
Билдер .conf файла для AmneziaWG / AmneziaWG 2.0.

ПРИОРИТЕТ 1: Если в vpn:// URI есть готовый raw_wg_config (из last_config.config) —
   возвращаем его как есть. Это гарантирует 100% совместимость с Amnezia-клиентом.

ПРИОРИТЕТ 2 (fallback): Если готового конфига нет — собираем вручную в ТОЧНОМ
   формате Amnezia API:
   [Interface]
   Address = ...
   DNS = ...
   PrivateKey = ...
   Jc = ...
   Jmin = ...
   Jmax = ...
   S1 = ...
   S2 = ...
   S3 = ...
   S4 = ...
   H1 = ...
   H2 = ...
   H3 = ...
   H4 = ...

   h1 = ...
   h2 = ...
   h3 = ...
   h4 = ...
   h5 = ...

   [Peer]
   PublicKey = ...
   PresharedKey = ...
   AllowedIPs = ...
   Endpoint = ...
   PersistentKeepalive = ...
"""
import logging
from typing import Optional
from utils.vpn_parser import AmneziaWGConfig, is_valid_amneziawg_config

logger = logging.getLogger(__name__)


def build_amneziawg_config(config: AmneziaWGConfig) -> Optional[str]:
    if not is_valid_amneziawg_config(config):
        logger.warning("build_amneziawg_config: недостаточно данных")
        return None

    if config.raw_wg_config and config.raw_wg_config.strip():
        logger.debug("build_amneziawg_config: using raw_wg_config from last_config")
        return config.raw_wg_config

    logger.debug("build_amneziawg_config: fallback manual build")
    return _build_manual(config)


def _build_manual(config: AmneziaWGConfig) -> str:
    is_awg2 = (
        config.protocol == "amneziawg2" or
        config.protocol_version == "2" or
        config.Jc or config.Jmin or config.Jmax
    )

    lines = []

    # ============ [Interface] ============
    lines.append("[Interface]")
    lines.append(f"Address = {config.address or '10.8.0.2/32'}")
    lines.append(f"DNS = {config.dns or '1.1.1.1, 1.0.0.1'}")
    lines.append(f"PrivateKey = {config.private_key}")

    # J-параметры
    if is_awg2:
        if config.Jc:
            lines.append(f"Jc = {config.Jc}")
        if config.Jmin:
            lines.append(f"Jmin = {config.Jmin}")
        if config.Jmax:
            lines.append(f"Jmax = {config.Jmax}")
    else:
        if config.J1:
            lines.append(f"J1 = {config.J1}")
        if config.J2:
            lines.append(f"J2 = {config.J2}")
        if config.J3:
            lines.append(f"J3 = {config.J3}")

    # S-параметры
    if config.S1:
        lines.append(f"S1 = {config.S1}")
    if config.S2:
        lines.append(f"S2 = {config.S2}")
    if config.S3:
        lines.append(f"S3 = {config.S3}")
    if config.S4:
        lines.append(f"S4 = {config.S4}")

    # H1-H4
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

    # Пустая строка перед h1-h5
    lines.append("")

    # h1-h5 (lowercase) — формат Amnezia API
    lines.append(f"h1 = {config.I1 or ''}")
    lines.append(f"h2 = {config.I2 or ''}")
    lines.append(f"h3 = {config.I3 or ''}")
    lines.append(f"h4 = {config.I4 or ''}")
    lines.append(f"h5 = {config.I5 or ''}")

    # Пустая строка перед [Peer]
    lines.append("")

    # ============ [Peer] ============
    lines.append("[Peer]")
    lines.append(f"PublicKey = {config.peer_public_key}")

    if config.peer_preshared_key:
        lines.append(f"PresharedKey = {config.peer_preshared_key}")

    allowed_ips = config.peer_allowed_ips or "0.0.0.0/0, ::/0"
    lines.append(f"AllowedIPs = {allowed_ips}")
    lines.append(f"Endpoint = {config.peer_endpoint}")
    lines.append(f"PersistentKeepalive = {config.peer_persistent_keepalive or 25}")

    return "\n".join(lines) + "\n"


def _format_h_value(value) -> str:
    if isinstance(value, int) and value != 0:
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def build_amneziawg_config_from_uri(vpn_uri: str) -> Optional[str]:
    from utils.vpn_parser import parse_vpn_uri
    parsed = parse_vpn_uri(vpn_uri)
    if parsed is None:
        return None
    return build_amneziawg_config(parsed)