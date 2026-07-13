import base64
import json
import zlib
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

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


def _try_decode_base64(payload: str) -> Optional[bytes]:
    """Пытается декодировать base64 в разных форматах"""
    # Попытка 1: standard base64
    try:
        return base64.b64decode(payload, validate=False)
    except Exception:
        pass
    
    # Попытка 2: urlsafe base64 с padding
    try:
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload_padded = payload + "=" * padding
        else:
            payload_padded = payload
        return base64.urlsafe_b64decode(payload_padded)
    except Exception:
        pass
    
    # Попытка 3: urlsafe base64 без padding
    try:
        return base64.urlsafe_b64decode(payload + "==")
    except Exception:
        pass
    
    return None


def _try_decompress(data: bytes) -> Optional[str]:
    """Пытается распаковать данные разными методами и декодировать в JSON"""
    decompressed = None
    
    # Попытка 1: zlib (стандартный)
    try:
        decompressed = zlib.decompress(data)
    except Exception:
        pass
    
    # Попытка 2: raw deflate (без zlib header)
    if decompressed is None:
        try:
            decompressed = zlib.decompress(data, -zlib.MAX_WBITS)
        except Exception:
            pass
    
    # Попытка 3: gzip
    if decompressed is None:
        try:
            decompressed = zlib.decompress(data, zlib.MAX_WBITS | 16)
        except Exception:
            pass
    
    if decompressed is None:
        return None
    
    # Перебор кодировок
    for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
        try:
            json_str = decompressed.decode(encoding)
            if json_str.strip().startswith("{"):
                return json_str
        except (UnicodeDecodeError, AttributeError):
            continue
    
    return None


def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    """
    Парсит vpn:// URI от Amnezia API.
    Поддерживает все возможные форматы кодирования и сжатия.
    """
    if not uri or not isinstance(uri, str):
        logger.warning("parse_vpn_uri: пустой или некорректный URI")
        return None
    
    # Убираем префикс vpn://
    if uri.startswith("vpn://"):
        payload = uri[6:]
    else:
        logger.warning("parse_vpn_uri: URI не начинается с vpn://")
        return None
    
    if not payload:
        logger.warning("parse_vpn_uri: пустой payload после vpn://")
        return None
    
    # Декодируем base64
    decoded_bytes = _try_decode_base64(payload)
    if decoded_bytes is None:
        logger.error("parse_vpn_uri: не удалось декодировать base64")
        return None
    
    # Пробуем распаковать
    json_str = _try_decompress(decoded_bytes)
    
    # Если не распаковалось, может быть это просто JSON в base64?
    if json_str is None:
        for encoding in ("utf-8", "utf-16", "cp1251", "latin-1"):
            try:
                json_str = decoded_bytes.decode(encoding)
                if json_str.strip().startswith("{"):
                    break
                else:
                    json_str = None
            except (UnicodeDecodeError, AttributeError):
                continue
    
    if not json_str:
        logger.error("parse_vpn_uri: не удалось получить JSON строку")
        return None
    
    # Парсим JSON
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"parse_vpn_uri: JSON decode error: {e}")
        return None
    
    if not isinstance(data, dict):
        logger.error("parse_vpn_uri: JSON не является словарем")
        return None
    
    return _extract_config(data, original_uri=uri)


def _find_awg_section(data: dict) -> Optional[tuple[dict, str]]:
    """
    Рекурсивно ищет секцию AmneziaWG в JSON.
    Возвращает (секция, протокол) или None.
    """
    # Прямой поиск в containers
    containers = data.get("containers", [])
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            for proto_key in ("amneziawg2", "amneziawg", "awg"):
                if proto_key in container and isinstance(container[proto_key], dict):
                    return container[proto_key], proto_key
    
    # Прямой поиск на верхнем уровне (если нет containers)
    for proto_key in ("amneziawg2", "amneziawg", "awg"):
        if proto_key in data and isinstance(data[proto_key], dict):
            return data[proto_key], proto_key
    
    # Рекурсивный поиск (fallback)
    def _recursive_search(obj, depth=0):
        if depth > 5:  # Защита от бесконечной рекурсии
            return None
        if isinstance(obj, dict):
            for proto_key in ("amneziawg2", "amneziawg", "awg"):
                if proto_key in obj and isinstance(obj[proto_key], dict):
                    return obj[proto_key], proto_key
            for value in obj.values():
                result = _recursive_search(value, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = _recursive_search(item, depth + 1)
                if result:
                    return result
        return None
    
    return _recursive_search(data)


def _extract_config(data: dict, original_uri: str) -> Optional[AmneziaWGConfig]:
    """Извлекает конфигурацию из JSON данных"""
    config = AmneziaWGConfig(raw_config=original_uri)
    
    # Базовые поля
    config.description = data.get("description", "") or ""
    config.host_name = data.get("hostName", "") or data.get("name", "") or ""
    
    # DNS
    dns1 = data.get("dns1") or "1.1.1.1"
    dns2 = data.get("dns2") or "8.8.8.8"
    config.dns = f"{dns1}, {dns2}"
    
    # Ищем секцию AmneziaWG
    awg_result = _find_awg_section(data)
    if awg_result is None:
        logger.error("_extract_config: не найдена секция AmneziaWG")
        return None
    
    awg_section, protocol_found = awg_result
    config.protocol = protocol_found
    
    # Парсим WireGuard config
    base_config = awg_section.get("config", "") or ""
    if base_config:
        _parse_wg_config(base_config, config)
    
    # Извлекаем обфускационные параметры
    for field_name in ("H1", "H2", "H3", "H4", "S1", "S2", "J1", "J2", "J3", "Jc", "Jmin", "Jmax"):
        value = awg_section.get(field_name)
        if value is not None:
            try:
                setattr(config, field_name, int(value))
            except (ValueError, TypeError):
                pass
    
    # Сохраняем дополнительные поля
    skip_keys = {"config", "H1", "H2", "H3", "H4", "S1", "S2", "J1", "J2", "J3", "Jc", "Jmin", "Jmax"}
    config.extra = {k: v for k, v in awg_section.items() if k not in skip_keys}
    
    return config


def _parse_wg_config(wg_config: str, config: AmneziaWGConfig) -> None:
    """Парсит WireGuard конфиг строку"""
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
    """
    Смягченная валидация: если есть базовые ключи WG, мы ОБЯЗАНЫ выдать .conf файл.
    Обфускационные параметры (H1-H4, S1-S2, Jc/Jmin/Jmax) опциональны.
    """
    if config is None:
        return False
    
    # Минимальные требования для WireGuard
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    
    return all(required)