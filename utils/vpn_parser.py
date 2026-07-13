"""
Парсер vpn:// URI от Amnezia API.
Формат: base64url(4-byte big-endian original_length + zlib_compressed_JSON)
Главные функции:
- decode_vpn_uri_to_json(uri) -> dict: возвращает весь JSON как словарь
- build_vpn_file(uri) -> str: возвращает готовый .vpn (JSON с отступами)
- build_conf_file(uri) -> str: возвращает готовый .conf (WireGuard INI из last_config)
"""
import base64
import json
import zlib
import struct
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)

@dataclass
class AmneziaWGConfig:
    protocol: str = "amneziawg2"
    address: str = ""
    private_key: str = ""
    dns: str = ""
    peer_public_key: str = ""
    peer_preshared_key: str = ""
    peer_allowed_ips: str = "0.0.0.0/0, ::/0"
    peer_endpoint: str = ""
    peer_persistent_keepalive: int = 25
    mtu: Optional[int] = None
    H1: Union[int, str] = 0
    H2: Union[int, str] = 0
    H3: Union[int, str] = 0
    H4: Union[int, str] = 0
    S1: int = 0
    S2: int = 0
    S3: int = 0
    S4: int = 0
    J1: int = 0
    J2: int = 0
    J3: int = 0
    Jc: int = 0
    Jmin: int = 0
    Jmax: int = 0
    I1: str = ""
    I2: str = ""
    I3: str = ""
    I4: str = ""
    I5: str = ""
    protocol_version: str = "2"
    description: str = ""
    host_name: str = ""
    raw_config: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

def _decode_base64url(payload: str) -> Optional[bytes]:
    try:
        b64 = payload.replace("-", "+").replace("_", "/")
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        return base64.b64decode(b64, validate=True)  # 🔥 Strict validation
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        return None

def _decompress_amnezia_format(data: bytes) -> Optional[str]:
    if len(data) < 4:
        return None
    try:
        original_length = struct.unpack(">I", data[:4])[0]
    except struct.error:
        logger.warning("_decompress_amnezia_format: bad header")
        return None
        
    compressed = data[4:]
    try:
        decompressed = zlib.decompress(compressed)
        if len(decompressed) != original_length:
            logger.warning(
                f"Length mismatch: header says {original_length}, "
                f"got {len(decompressed)}"
            )
        return decompressed.decode("utf-8")
    except Exception as e:
        logger.warning(f"_decompress_amnezia_format zlib failed: {e}")
        return None

def decode_vpn_uri_to_json(uri: str) -> Optional[dict]:
    if not uri or not isinstance(uri, str):
        return None
    payload = uri[6:] if uri.startswith("vpn://") else None
    if not payload:
        return None
    decoded = _decode_base64url(payload)
    if decoded is None:
        logger.error("decode_vpn_uri_to_json: base64url decode failed")
        return None
    json_str = _decompress_amnezia_format(decoded)
    if json_str is None:
        logger.error("decode_vpn_uri_to_json: zlib decompress failed")
        return None
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"decode_vpn_uri_to_json: JSON parse error: {e}")
        return None
    if not isinstance(data, dict):
        logger.error("decode_vpn_uri_to_json: JSON is not a dict")
        return None
    return data

def build_vpn_file(uri: str) -> Optional[str]:
    """
    🔥 Создаёт содержимое .vpn файла (для основного клиента Amnezia).
    Возвращает ВЕСЬ JSON как строку с красивым форматированием (indent=2).
    """
    data = decode_vpn_uri_to_json(uri)
    if data is None:
        return None
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

def build_conf_file(uri: str) -> Optional[str]:
    """
    🔥 Создаёт содержимое .conf файла (для AmneziaWG).
    Извлекает готовый WireGuard INI из awg.last_config.config.
    """
    data = decode_vpn_uri_to_json(uri)
    if data is None:
        return None
    try:
        containers = data.get("containers", [])
        if not containers:
            return None
        awg = containers[0].get("awg", {})
        last_config_str = awg.get("last_config")
        if not last_config_str:
            return None
        last_config = json.loads(last_config_str)
        return last_config.get("config")
    except Exception as e:
        logger.error(f"build_conf_file failed: {e}")
        return None

# Оставлено для обратной совместимости
def build_conf_content(uri: str) -> Optional[str]:
    return build_vpn_file(uri)

def is_valid_vpn_uri(uri: str) -> bool:
    data = decode_vpn_uri_to_json(uri)
    if not data or not isinstance(data, dict):
        return False
    containers = data.get("containers")
    if not containers or not isinstance(containers, list):
        return False
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("awg", "amneziawg2", "amneziawg", "awg2"):
            if key in container and isinstance(container[key], dict):
                return True
    return False

# ============================================================
# СТАРЫЕ ФУНКЦИИ — для обратной совместимости
# ============================================================
def _parse_h_value(value: Any) -> Union[int, str]:
    if value is None: return 0
    if isinstance(value, int): return value
    if isinstance(value, str):
        if "-" in value: return value
        try: return int(value)
        except ValueError: return value
    try: return int(value)
    except (ValueError, TypeError): return str(value)

def _parse_int_value(value: Any, default: int = 0) -> int:
    if value is None: return default
    try: return int(value)
    except (ValueError, TypeError): return default

def _extract_awg_section(container: dict) -> Optional[dict]:
    for key in ("awg", "amneziawg2", "amneziawg", "awg2"):
        if key in container and isinstance(container[key], dict):
            return container[key]
    return None

def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    data = decode_vpn_uri_to_json(uri)
    if data is None: return None
    return _build_config_object(data, original_uri=uri)

def _build_config_object(data: dict, original_uri: str) -> Optional[AmneziaWGConfig]:
    cfg = AmneziaWGConfig(raw_config=original_uri)
    cfg.description = data.get("description", "") or ""
    cfg.host_name = data.get("hostName") or data.get("hostname") or data.get("host_name") or ""
    dns1 = data.get("dns1") or "1.1.1.1"
    dns2 = data.get("dns2") or "1.0.0.1"
    cfg.dns = f"{dns1}, {dns2}"
    containers = data.get("containers")
    awg_section = None
    if isinstance(containers, list) and containers:
        for container in containers:
            if isinstance(container, dict):
                awg_section = _extract_awg_section(container)
                if awg_section: break
    if awg_section is None:
        awg_section = _extract_awg_section(data)
    if awg_section is None:
        logger.error("_build_config_object: AWG section not found")
        return None
        
    cfg.protocol_version = str(awg_section.get("protocol_version", "2"))
    cfg.protocol = "amneziawg2" if cfg.protocol_version == "2" else "amneziawg"
    
    last_config_raw = awg_section.get("last_config")
    last_config = None
    if last_config_raw:
        if isinstance(last_config_raw, str):
            try: last_config = json.loads(last_config_raw)
            except json.JSONDecodeError: pass
        elif isinstance(last_config_raw, dict):
            last_config = last_config_raw
            
    if last_config and isinstance(last_config, dict):
        if not cfg.address:
            client_ip = last_config.get("client_ip")
            if client_ip: cfg.address = f"{client_ip}/32"
        if not cfg.private_key: cfg.private_key = last_config.get("client_priv_key") or ""
        if not cfg.peer_public_key: cfg.peer_public_key = last_config.get("server_pub_key") or ""
        if not cfg.peer_preshared_key: cfg.peer_preshared_key = last_config.get("psk_key") or ""
        if not cfg.peer_endpoint:
            hostname = last_config.get("hostName") or cfg.host_name
            port = last_config.get("port")
            if hostname and port: cfg.peer_endpoint = f"{hostname}:{port}"
        if cfg.mtu is None:
            mtu_raw = last_config.get("mtu")
            if mtu_raw:
                try: cfg.mtu = int(mtu_raw)
                except (ValueError, TypeError): pass
        allowed_ips_list = last_config.get("allowed_ips")
        if isinstance(allowed_ips_list, list) and allowed_ips_list:
            cfg.peer_allowed_ips = ", ".join(allowed_ips_list)
            
    for field_name in ("H1", "H2", "H3", "H4"):
        if not getattr(cfg, field_name):
            setattr(cfg, field_name, _parse_h_value(awg_section.get(field_name)))
    cfg.S1 = cfg.S1 or _parse_int_value(awg_section.get("S1"))
    cfg.S2 = cfg.S2 or _parse_int_value(awg_section.get("S2"))
    cfg.S3 = cfg.S3 or _parse_int_value(awg_section.get("S3"))
    cfg.S4 = cfg.S4 or _parse_int_value(awg_section.get("S4"))
    cfg.J1 = cfg.J1 or _parse_int_value(awg_section.get("J1"))
    cfg.J2 = cfg.J2 or _parse_int_value(awg_section.get("J2"))
    cfg.J3 = cfg.J3 or _parse_int_value(awg_section.get("J3"))
    cfg.Jc = cfg.Jc or _parse_int_value(awg_section.get("Jc"))
    cfg.Jmin = cfg.Jmin or _parse_int_value(awg_section.get("Jmin"))
    cfg.Jmax = cfg.Jmax or _parse_int_value(awg_section.get("Jmax"))
    for field_name in ("I1", "I2", "I3", "I4", "I5"):
        if not getattr(cfg, field_name):
            value = awg_section.get(field_name)
            if value is not None: setattr(cfg, field_name, str(value))
    return cfg

def is_valid_amneziawg_config(config: Optional[AmneziaWGConfig]) -> bool:
    if config is None: return False
    required = [config.private_key, config.peer_public_key, config.peer_endpoint]
    return all(required)
