import base64
import json
import zlib
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

@dataclass
class AmneziaWGConfig:
    """Распарсенная конфигурация AmneziaWG"""
    protocol: str = "amneziawg2"
    address: str = ""
    private_key: str = ""
    dns: str = ""
    peer_public_key: str = ""
    peer_preshared_key: str = ""
    peer_allowed_ips: str = "0.0.0.0/0, ::/0"
    peer_endpoint: str = ""
    peer_persistent_keepalive: int = 25
    H1: int = 0
    H2: int = 0
    H3: int = 0
    H4: int = 0
    S1: int = 0
    S2: int = 0
    J1: int = 0
    J2: int = 0
    J3: int = 0
    Jc: int = 0
    Jmin: int = 0
    Jmax: int = 0
    description: str = ""
    host_name: str = ""
    raw_config: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    if not uri or not isinstance(uri, str):
        return None
        
    if uri.startswith("vpn://"):
        payload = uri[6:]
    else:
        return None
        
    if not payload:
        return None

    decoded_bytes = None
    
    # Попытка 1: standard base64
    try:
        decoded_bytes = base64.b64decode(payload, validate=False)
    except Exception:
        pass
        
    # Попытка 2: urlsafe base64
    if decoded_bytes is None:
        try:
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload_padded = payload + "=" * padding
            else:
                payload_padded = payload
            decoded_bytes = base64.urlsafe_b64decode(payload_padded)
        except Exception:
            pass
            
    # Попытка 3: без padding вообще
    if decoded_bytes is None:
        try:
            decoded_bytes = base64.urlsafe_b64decode(payload + "==")
        except Exception:
            pass

    # Попытка 4: zlib/deflate (некоторые клиенты Amnezia сжимают конфиги)
    if decoded_bytes is None:
        try:
            decoded_bytes = zlib.decompress(base64.b64decode(payload))
        except Exception:
            pass
        if decoded_bytes is None:
            try:
                decoded_bytes = zlib.decompress(base64.urlsafe_b64decode(payload + "=="))
            except Exception:
                pass

    if decoded_bytes is None:
        return None

    # 🔥 ИСПРАВЛЕНО: Перебор кодировок (UTF-8, Windows-1251, Latin-1)
    # Ошибка 0xe2 часто возникает, когда API отдаёт кириллицу или спецсимволы в cp1251
    json_str = None
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            json_str = decoded_bytes.decode(encoding)
            if json_str.strip().startswith("{"):
                break
        except (UnicodeDecodeError, AttributeError):
            continue
            
    if not json_str:
        return None

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return None
        
    if not isinstance(data, dict):
        return None

    return _extract_config(data, original_uri=uri)

def _extract_config(data: dict, original_uri: str) -> Optional[AmneziaWGConfig]:
    config = AmneziaWGConfig(raw_config=original_uri)
    
    config.description = data.get("description", "") or ""
    config.host_name = data.get("hostName", "") or ""
    dns1 = data.get("dns1") or "8.8.8.8"
    dns2 = data.get("dns2") or "8.8.4.4"
    config.dns = f"{dns1}, {dns2}"

    containers = data.get("containers", [])
    if not containers or not isinstance(containers, list):
        return None

    container = containers[0]
    if not isinstance(container, dict):
        return None

    awg_section = None
    protocol_found = "amneziawg2"
    for proto_key in ("amneziawg2", "amneziawg", "awg"):
        if proto_key in container and isinstance(container[proto_key], dict):
            awg_section = container[proto_key]
            protocol_found = proto_key
            break

    if awg_section is None:
        return None

    config.protocol = protocol_found

    base_config = awg_section.get("config", "") or ""
    if base_config:
        _parse_wg_config(base_config, config)

    for field_name in ("H1", "H2", "H3", "H4", "S1", "S2", "J1", "J2", "J3", "Jc", "Jmin", "Jmax"):
        value = awg_section.get(field_name)
        if value is not None:
            try:
                setattr(config, field_name, int(value))
            except (ValueError, TypeError):
                pass

    skip_keys = {"config", "H1", "H2", "H3", "H4", "S1", "S2", "J1", "J2", "J3", "Jc", "Jmin", "Jmax"}
    config.extra = {k: v for k, v in awg_section.items() if k not in skip_keys}

    return config

def _parse_wg_config(wg_config: str, config: AmneziaWGConfig) -> None:
    current_section = None
    for line in wg_config.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if current_section == "interface":
            if key == "address":
                config.address = value
            elif key == "privatekey":
                config.private_key = value
            elif key == "dns":
                config.dns = value
        elif current_section == "peer":
            if key == "publickey":
                config.peer_public_key = value
            elif key == "presharedkey":
                config.peer_preshared_key = value
            elif key == "allowedips":
                config.peer_allowed_ips = value
            elif key == "endpoint":
                config.peer_endpoint = value
            elif key == "persistentkeepalive":
                try:
                    config.peer_persistent_keepalive = int(value)
                except ValueError:
                    pass

def is_valid_amneziawg_config(config: Optional[AmneziaWGConfig]) -> bool:
    if config is None:
        return False
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    return all(required)