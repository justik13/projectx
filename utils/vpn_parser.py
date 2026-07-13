"""
Парсер vpn:// URI от Amnezia API.
Формат: base64url(4-byte big-endian original_length + zlib_compressed_JSON)
Поддержка AmneziaWG и AmneziaWG 2.0.
"""
import base64
import json
import zlib
import struct
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


def _decode_base64url(payload: str) -> Optional[bytes]:
    """
    Декодирует base64url (формат Amnezia):
    - Заменяет - на + и _ на /
    - Добавляет padding =
    - Декодирует
    """
    try:
        # base64url → base64 standard
        b64 = payload.replace("-", "+").replace("_", "/")
        
        # Добавляем padding
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        
        return base64.b64decode(b64)
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        return None


def _try_standard_base64(payload: str) -> Optional[bytes]:
    """Попытка обычного base64 (если вдруг API изменился)"""
    try:
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        return base64.b64decode(payload, validate=False)
    except Exception:
        return None


def _try_decompress_amnezia(data: bytes) -> Optional[str]:
    """
    Распаковывает формат Amnezia: 4-byte header + zlib data
    """
    if len(data) < 4:
        return None
    
    # Читаем заголовок — 4 байта big-endian = оригинальная длина JSON
    try:
        original_length = struct.unpack(">I", data[:4])[0]
    except struct.error:
        logger.warning("_try_decompress_amnezia: не могу прочитать заголовок")
        return None
    
    # Берём сжатые данные (после 4 байт заголовка)
    compressed = data[4:]
    
    try:
        # standard zlib deflate
        decompressed_bytes = zlib.decompress(compressed)
        
        # Проверяем длину (опционально, но полезно для диагностики)
        if len(decompressed_bytes) != original_length:
            logger.warning(
                f"Length mismatch: header says {original_length}, got {len(decompressed_bytes)}"
            )
        
        # Декодируем в строку
        return decompressed_bytes.decode("utf-8")
    except Exception as e:
        logger.warning(f"_try_decompress_amnezia zlib failed: {e}")
        return None


def _try_plain_decompress(data: bytes) -> Optional[str]:
    """
    Попытка стандартного zlib (без заголовка длины)
    """
    for wbits in (15, -15, 31, 47):
        try:
            decompressed = zlib.decompress(data, wbits)
            text = decompressed.decode("utf-8")
            if text.strip().startswith("{"):
                return text
        except Exception:
            continue
    return None


def _try_plain_json(data: bytes) -> Optional[str]:
    """
    Попытка: данные вообще не сжаты — это просто base64-encoded JSON
    """
    try:
        text = data.decode("utf-8")
        if text.strip().startswith("{"):
            return text
    except UnicodeDecodeError:
        pass
    return None


def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    """
    Парсит vpn:// URI от Amnezia API.
    Формат: base64url(4-byte big-endian length + zlib(JSON))
    Fallback: обычный base64 + JSON или zlib
    """
    if not uri or not isinstance(uri, str):
        return None
    
    # Убираем префикс
    if uri.startswith("vpn://"):
        payload = uri[6:]
    else:
        return None
    
    if not payload:
        return None
    
    # Декодируем base64url
    decoded = _decode_base64url(payload)
    if decoded is None:
        # Fallback: обычный base64
        decoded = _try_standard_base64(payload)
    
    if decoded is None:
        logger.error("parse_vpn_uri: не удалось декодировать base64/base64url")
        return None
    
    # Пробуем формат Amnezia (4-byte header + zlib)
    json_str = _try_decompress_amnezia(decoded)
    if json_str:
        return _parse_and_extract(json_str, uri)
    
    # Пробуем обычный zlib
    json_str = _try_plain_decompress(decoded)
    if json_str:
        return _parse_and_extract(json_str, uri)
    
    # Пробуем plain JSON (не сжатый)
    json_str = _try_plain_json(decoded)
    if json_str:
        return _parse_and_extract(json_str, uri)
    
    logger.error("parse_vpn_uri: ни один метод распаковки не сработал")
    return None


def _parse_and_extract(json_str: str, original_uri: str) -> Optional[AmneziaWGConfig]:
    """Парсит JSON строку и извлекает конфиг"""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"_parse_and_extract JSON error: {e}")
        return None
    
    if not isinstance(data, dict):
        logger.error("_parse_and_extract: JSON не словарь")
        return None
    
    return _extract_config(data, original_uri)


def _find_awg_section(data: dict) -> Optional[tuple[dict, str]]:
    """
    Рекурсивно ищет секцию AmneziaWG в JSON.
    Возвращает (секция, протокол) или None.
    """
    # 1) Прямой поиск в containers
    containers = data.get("containers", [])
    if isinstance(containers, list):
        for container in containers:
            if not isinstance(container, dict):
                continue
            for proto_key in ("amneziawg2", "amneziawg", "awg"):
                if proto_key in container and isinstance(container[proto_key], dict):
                    return container[proto_key], proto_key
    
    # 2) Прямой поиск на верхнем уровне (flat формат)
    for proto_key in ("amneziawg2", "amneziawg", "awg"):
        if proto_key in data and isinstance(data[proto_key], dict):
            return data[proto_key], proto_key
    
    # 3) Рекурсивный поиск (fallback)
    def _recursive_search(obj, depth=0):
        if depth > 5:
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
    skip_keys = {"config", "H1", "H2", "H3", "H4", "S1", "S2", "J1", "J2", "J3", "Jc", "Jmin", "Jmax", "last_config"}
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
    """
    if config is None:
        return False
    
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    
    return all(required)