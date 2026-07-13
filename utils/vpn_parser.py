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
            "last_config": "<JSON string with pre-built WireGuard config>"
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
    # H1-H4: в AWG 1.0 это числа, в AWG 2.0 — строки-диапазоны "min-max"
    H1: Union[int, str] = 0
    H2: Union[int, str] = 0
    H3: Union[int, str] = 0
    H4: Union[int, str] = 0
    S1: int = 0
    S2: int = 0
    S3: int = 0  # AWG 2.0 only
    S4: int = 0  # AWG 2.0 only
    J1: int = 0  # AWG 1.0
    J2: int = 0  # AWG 1.0
    J3: int = 0  # AWG 1.0
    Jc: int = 0  # AWG 2.0
    Jmin: int = 0  # AWG 2.0
    Jmax: int = 0  # AWG 2.0
    
    # I1-I5: пакеты инициализации (AWG 2.0)
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
    raw_wg_config: str = ""  # Готовый WireGuard конфиг из last_config
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
    """
    Декодирует vpn:// URI и возвращает JSON dict.
    """
    if not uri or not isinstance(uri, str):
        return None
    
    payload = uri[6:] if uri.startswith("vpn://") else None
    if not payload:
        return None
    
    # Пытаемся base64url (основной формат)
    decoded = _decode_base64url(payload)
    if decoded is None:
        decoded = _try_standard_base64(payload)
    
    if decoded is None:
        logger.error("_parse_vpn_json: base64 decode failed")
        return None
    
    # Пытаемся формат Amnezia (4-byte header + zlib)
    json_str = _decompress_amnezia_format(decoded)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # Fallback: plain zlib
    json_str = _decompress_plain_zlib(decoded)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # Fallback: plain JSON (не сжатый)
    try:
        text = decoded.decode("utf-8")
        if text.strip().startswith("{"):
            return json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    
    logger.error("_parse_vpn_json: no decoding method worked")
    return None


def _parse_h_value(value: Any) -> Union[int, str]:
    """
    Парсит значение H1-H4.
    В AWG 1.0 это int, в AWG 2.0 — строка вида "min-max" (диапазон).
    """
    if value is None:
        return 0
    
    if isinstance(value, int):
        return value
    
    if isinstance(value, str):
        # Если это диапазон "min-max" — сохраняем как есть
        if "-" in value:
            return value
        # Если это просто число в строке
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
    """
    Извлекает AWG секцию из контейнера.
    Реальный ключ: "awg". Fallback: "amneziawg2", "amneziawg".
    """
    for key in ("awg", "amneziawg2", "amneziawg", "awg2"):
        if key in container and isinstance(container[key], dict):
            return container[key]
    return None


def _parse_last_config(last_config: Any) -> Optional[dict]:
    """
    Парсит поле last_config.
    Обычно это JSON-строка, содержащая полный конфиг с WireGuard.
    """
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
    """Парсит готовый WireGuard конфиг из last_config.config"""
    current_section = None
    for line in config_str.splitlines():
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
    """
    Главная точка входа: парсит vpn:// URI и возвращает AmneziaWGConfig.
    """
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
    
    # Fallback: AWG секция на верхнем уровне
    if awg_section is None:
        awg_section = _extract_awg_section(data)
    
    if awg_section is None:
        logger.error("_build_config_object: AWG section not found")
        return None
    
    # Protocol version
    cfg.protocol_version = str(awg_section.get("protocol_version", "2"))
    cfg.protocol = "amneziawg2" if cfg.protocol_version == "2" else "amneziawg"
    
    # ============ САМАЯ ВАЖНАЯ ЧАСТЬ: last_config ============
    # В Amnezia API 2.0 в поле last_config уже есть ГОТОВЫЙ WireGuard конфиг
    last_config = _parse_last_config(awg_section.get("last_config"))
    
    if last_config and isinstance(last_config, dict):
        # Извлекаем готовый WireGuard конфиг
        raw_wg = last_config.get("config") or ""
        if raw_wg:
            cfg.raw_wg_config = raw_wg
            _parse_raw_wg_config(raw_wg, cfg)
        
        # Извлекаем дополнительные поля из last_config
        cfg.description = (
            cfg.description or
            last_config.get("hostName") or
            last_config.get("description") or
            ""
        )
    
    # ============ Обфускационные параметры ============
    # H1-H4: могут быть числами (AWG 1.0) или строками-диапазонами (AWG 2.0)
    for field_name in ("H1", "H2", "H3", "H4"):
        setattr(cfg, field_name, _parse_h_value(awg_section.get(field_name)))
    
    # S1-S4: целые числа
    cfg.S1 = _parse_int_value(awg_section.get("S1"))
    cfg.S2 = _parse_int_value(awg_section.get("S2"))
    cfg.S3 = _parse_int_value(awg_section.get("S3"))
    cfg.S4 = _parse_int_value(awg_section.get("S4"))
    
    # J-параметры
    cfg.J1 = _parse_int_value(awg_section.get("J1"))
    cfg.J2 = _parse_int_value(awg_section.get("J2"))
    cfg.J3 = _parse_int_value(awg_section.get("J3"))
    cfg.Jc = _parse_int_value(awg_section.get("Jc"))
    cfg.Jmin = _parse_int_value(awg_section.get("Jmin"))
    cfg.Jmax = _parse_int_value(awg_section.get("Jmax"))
    
    # I1-I5: строки с пакетами инициализации (AWG 2.0)
    for field_name in ("I1", "I2", "I3", "I4", "I5"):
        value = awg_section.get(field_name)
        if value is not None:
            setattr(cfg, field_name, str(value))
    
    # MTU
    if cfg.mtu is None:
        mtu_raw = awg_section.get("mtu") or (last_config or {}).get("mtu")
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
    """
    Смягчённая валидация: если есть базовые ключи WG, выдаём .conf.
    """
    if config is None:
        return False
    
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    
    return all(required)