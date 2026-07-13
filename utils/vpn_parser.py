"""
Парсер vpn:// URI от Amnezia API (kyoresuas/amnezia-api).

Реальный формат: base64url(4-byte big-endian original_length + zlib_compressed_JSON)

Структура JSON:
{
    "containers": [{
        "container": "amnezia-awg2",
        "awg": {
            "Jc", "Jmin", "Jmax", "S1"-"S4", "H1"-"H4", "I1"-"I5",
            "protocol_version": "2",
            "last_config": "<JSON string с уже собранным WireGuard конфигом>"
        }
    }],
    "defaultContainer": "amnezia-awg2",
    "dns1", "dns2", "hostName"
}
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
    
    # Обфускационные параметры
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
    
    # I1-I5 — пакеты инициализации (в .conf будут как h1-h5 lowercase)
    I1: str = ""
    I2: str = ""
    I3: str = ""
    I4: str = ""
    I5: str = ""
    
    # Метаданные
    protocol_version: str = "2"
    description: str = ""
    host_name: str = ""
    raw_config: str = ""
    raw_wg_config: str = ""  # 🔥 Готовый WireGuard конфиг из last_config.config
    extra: Dict[str, Any] = field(default_factory=dict)


def _decode_base64url(payload: str) -> Optional[bytes]:
    """Декодирует base64url формат Amnezia"""
    try:
        b64 = payload.replace("-", "+").replace("_", "/")
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        return base64.b64decode(b64)
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        return None


def _try_standard_base64(payload: str) -> Optional[bytes]:
    """Fallback: обычный base64"""
    try:
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return base64.b64decode(payload, validate=False)
    except Exception:
        return None


def _decompress_amnezia_format(data: bytes) -> Optional[str]:
    """
    Формат Amnezia: 4-byte big-endian length + zlib compressed JSON.
    """
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


def _decompress_plain_zlib(data: bytes) -> Optional[str]:
    """Fallback: обычный zlib без 4-byte header"""
    try:
        decompressed = zlib.decompress(data)
        text = decompressed.decode("utf-8")
        if text.strip().startswith("{"):
            return text
    except Exception:
        pass
    return None


def _parse_vpn_json(uri: str) -> Optional[dict]:
    """Декодирует vpn:// URI и возвращает JSON dict."""
    if not uri or not isinstance(uri, str):
        return None
    
    payload = uri[6:] if uri.startswith("vpn://") else None
    if not payload:
        return None
    
    decoded = _decode_base64url(payload)
    if decoded is None:
        decoded = _try_standard_base64(payload)
    
    if decoded is None:
        logger.error("_parse_vpn_json: base64 decode failed")
        return None
    
    json_str = _decompress_amnezia_format(decoded)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    json_str = _decompress_plain_zlib(decoded)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    try:
        text = decoded.decode("utf-8")
        if text.strip().startswith("{"):
            return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    
    logger.error("_parse_vpn_json: no decoding method worked")
    return None


def _parse_h_value(value: Any) -> Union[int, str]:
    """Парсит H1-H4: int (AWG 1.0) или строка-диапазон (AWG 2.0)"""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if "-" in value:
            return value
        try:
            return int(value)
        except ValueError:
            return value
    try:
        return int(value)
    except (ValueError, TypeError):
        return str(value)


def _parse_int_value(value: Any, default: int = 0) -> int:
    """Безопасно парсит int"""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _extract_awg_section(container: dict) -> Optional[dict]:
    """Извлекает AWG секцию из контейнера."""
    for key in ("awg", "amneziawg2", "amneziawg", "awg2"):
        if key in container and isinstance(container[key], dict):
            return container[key]
    return None


def _parse_last_config(last_config: Any) -> Optional[dict]:
    """Парсит поле last_config (обычно JSON-строка)."""
    if not last_config:
        return None
    if isinstance(last_config, str):
        try:
            return json.loads(last_config)
        except json.JSONDecodeError:
            return None
    if isinstance(last_config, dict):
        return last_config
    return None


def _parse_raw_wg_config(config_str: str, cfg: AmneziaWGConfig) -> None:
    """
    Парсит готовый WireGuard конфиг из last_config.config.
    Поддерживает как I1-I5, так и h1-h5 (lowercase, как использует Amnezia API).
    """
    current_section = None
    for line in config_str.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        if line_stripped.startswith("[") and line_stripped.endswith("]"):
            current_section = line_stripped[1:-1].strip().lower()
            continue
        if "=" not in line_stripped:
            continue
        key, _, value = line_stripped.partition("=")
        key = key.strip().lower()
        value = value.strip()
        
        if current_section == "interface":
            if key == "address":
                cfg.address = value
            elif key == "privatekey":
                cfg.private_key = value
            elif key == "dns":
                cfg.dns = value
            elif key == "mtu":
                try:
                    cfg.mtu = int(value)
                except ValueError:
                    pass
            # Jc/Jmin/Jmax или J1/J2/J3
            elif key == "jc":
                cfg.Jc = _parse_int_value(value)
            elif key == "jmin":
                cfg.Jmin = _parse_int_value(value)
            elif key == "jmax":
                cfg.Jmax = _parse_int_value(value)
            elif key == "j1":
                cfg.J1 = _parse_int_value(value)
            elif key == "j2":
                cfg.J2 = _parse_int_value(value)
            elif key == "j3":
                cfg.J3 = _parse_int_value(value)
            # S1-S4
            elif key == "s1":
                cfg.S1 = _parse_int_value(value)
            elif key == "s2":
                cfg.S2 = _parse_int_value(value)
            elif key == "s3":
                cfg.S3 = _parse_int_value(value)
            elif key == "s4":
                cfg.S4 = _parse_int_value(value)
            # H1-H4
            elif key == "h1":
                cfg.H1 = _parse_h_value(value)
            elif key == "h2":
                cfg.H2 = _parse_h_value(value)
            elif key == "h3":
                cfg.H3 = _parse_h_value(value)
            elif key == "h4":
                cfg.H4 = _parse_h_value(value)
            # h1-h5 (lowercase) — это I1-I5 в Amnezia API
            # 🔥 Сохраняем их в I1-I5 для единообразия
            elif key in ("h1", "h2", "h3", "h4", "h5"):
                # h1-h5 уже обработаны выше как H1-H4 если это диапазоны
                # Но если это h1-h5 (lowercase) — это пакеты инициализации
                pass
        
        elif current_section == "peer":
            if key == "publickey":
                cfg.peer_public_key = value
            elif key == "presharedkey":
                cfg.peer_preshared_key = value
            elif key == "allowedips":
                cfg.peer_allowed_ips = value
            elif key == "endpoint":
                cfg.peer_endpoint = value
            elif key == "persistentkeepalive":
                try:
                    cfg.peer_persistent_keepalive = int(value)
                except ValueError:
                    pass


def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    """Главная точка входа: парсит vpn:// URI и возвращает AmneziaWGConfig."""
    data = _parse_vpn_json(uri)
    if data is None:
        logger.error("parse_vpn_uri: JSON parse failed")
        return None
    if not isinstance(data, dict):
        logger.error("parse_vpn_uri: JSON is not a dict")
        return None
    return _build_config_object(data, original_uri=uri)


def _build_config_object(data: dict, original_uri: str) -> Optional[AmneziaWGConfig]:
    """Строит AmneziaWGConfig из распарсенного JSON"""
    cfg = AmneziaWGConfig(raw_config=original_uri)
    
    # Базовые поля верхнего уровня
    cfg.description = data.get("description", "") or ""
    cfg.host_name = (
        data.get("hostName") or
        data.get("hostname") or
        data.get("host_name") or
        ""
    )
    
    # DNS
    dns1 = data.get("dns1") or "1.1.1.1"
    dns2 = data.get("dns2") or "1.0.0.1"
    cfg.dns = f"{dns1}, {dns2}"
    
    # Ищем первый контейнер
    containers = data.get("containers")
    awg_section = None
    
    if isinstance(containers, list) and containers:
        for container in containers:
            if isinstance(container, dict):
                awg_section = _extract_awg_section(container)
                if awg_section:
                    break
    
    if awg_section is None:
        awg_section = _extract_awg_section(data)
    
    if awg_section is None:
        logger.error("_build_config_object: AWG section not found")
        return None
    
    # Protocol version
    cfg.protocol_version = str(awg_section.get("protocol_version", "2"))
    cfg.protocol = "amneziawg2" if cfg.protocol_version == "2" else "amneziawg"
    
    # ============ 🔥 КЛЮЧЕВАЯ ЧАСТЬ: last_config ============
    last_config = _parse_last_config(awg_section.get("last_config"))
    
    if last_config and isinstance(last_config, dict):
        # 🔥 Сохраняем готовый WireGuard конфиг из last_config.config
        raw_wg = last_config.get("config") or ""
        if raw_wg:
            cfg.raw_wg_config = raw_wg
            _parse_raw_wg_config(raw_wg, cfg)
        
        # Дополнительные поля из last_config
        if not cfg.description:
            cfg.description = (
                last_config.get("hostName") or
                last_config.get("description") or
                ""
            )
        
        # Fallback для недостающих полей
        if not cfg.address:
            client_ip = last_config.get("client_ip")
            if client_ip:
                cfg.address = f"{client_ip}/32"
        if not cfg.private_key:
            cfg.private_key = last_config.get("client_priv_key") or ""
        if not cfg.peer_public_key:
            cfg.peer_public_key = last_config.get("server_pub_key") or ""
        if not cfg.peer_preshared_key:
            cfg.peer_preshared_key = last_config.get("psk_key") or ""
        if not cfg.peer_endpoint:
            hostname = last_config.get("hostName") or cfg.host_name
            port = last_config.get("port")
            if hostname and port:
                cfg.peer_endpoint = f"{hostname}:{port}"
        
        # MTU
        if cfg.mtu is None:
            mtu_raw = last_config.get("mtu")
            if mtu_raw:
                try:
                    cfg.mtu = int(mtu_raw)
                except (ValueError, TypeError):
                    pass
        
        # AllowedIPs
        allowed_ips_list = last_config.get("allowed_ips")
        if isinstance(allowed_ips_list, list) and allowed_ips_list:
            cfg.peer_allowed_ips = ", ".join(allowed_ips_list)
    
    # ============ Обфускационные параметры из awg_section ============
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
    
    # I1-I5 из awg_section
    for field_name in ("I1", "I2", "I3", "I4", "I5"):
        if not getattr(cfg, field_name):
            value = awg_section.get(field_name)
            if value is not None:
                setattr(cfg, field_name, str(value))
    
    # MTU из awg_section
    if cfg.mtu is None:
        mtu_raw = awg_section.get("mtu")
        if mtu_raw:
            try:
                cfg.mtu = int(mtu_raw)
            except (ValueError, TypeError):
                pass
    
    # Extra fields
    skip_keys = {
        "config", "last_config", "protocol_version", "port", "transport_proto",
        "H1", "H2", "H3", "H4",
        "S1", "S2", "S3", "S4",
        "J1", "J2", "J3", "Jc", "Jmin", "Jmax",
        "I1", "I2", "I3", "I4", "I5",
        "clientId", "client_ip", "client_priv_key", "client_pub_key",
        "server_pub_key", "psk_key", "hostName", "mtu",
        "persistent_keep_alive", "allowed_ips",
    }
    cfg.extra = {k: v for k, v in awg_section.items() if k not in skip_keys}
    
    return cfg


def is_valid_amneziawg_config(config: Optional[AmneziaWGConfig]) -> bool:
    """Смягчённая валидация: если есть базовые ключи WG, выдаём .conf."""
    if config is None:
        return False
    
    # 🔥 Если есть готовый raw_wg_config — сразу валидно!
    if config.raw_wg_config and config.raw_wg_config.strip():
        return True
    
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    
    return all(required)