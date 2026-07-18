"""
Парсер vpn:// URI от Amnezia API.
Формат: base64url(4-byte big-endian original_length + zlib_compressed_JSON)

Главные функции:
- decode_vpn_uri_to_json(uri) -> dict: возвращает весь JSON как словарь
- build_vpn_file(uri) -> str: возвращает готовый .vpn (JSON с отступами)
- build_conf_file(uri) -> str: возвращает готовый .conf (WireGuard INI из last_config)
- is_valid_vpn_uri(uri) -> bool: проверяет protocol_version == "2"
"""

import base64
import json
import zlib
import struct
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def _decode_base64url(payload: str) -> Optional[bytes]:
    """Декодирует base64url строку в байты."""
    try:
        b64 = payload.replace("-", "+").replace("_", "/")
        padding_needed = len(b64) % 4
        if padding_needed:
            b64 += "=" * (4 - padding_needed)
        return base64.b64decode(b64, validate=True)
    except Exception as e:
        logger.warning(f"_decode_base64url failed: {e}")
        return None


def _decompress_amnezia_format(data: bytes) -> Optional[str]:
    """Декомпрессирует zlib-сжатый JSON с 4-byte header."""
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
    """
    Декодирует vpn:// URI в JSON словарь.
    
    Args:
        uri: Строка вида "vpn://..."
    
    Returns:
        dict с распарсенным JSON или None при ошибке
    """
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
    Создаёт содержимое .vpn файла (для основного клиента Amnezia).
    Возвращает ВЕСЬ JSON как строку с красивым форматированием (indent=2).
    """
    data = decode_vpn_uri_to_json(uri)
    if data is None:
        return None
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build_conf_file(uri: str) -> Optional[str]:
    """
    Создаёт содержимое .conf файла (для AmneziaWG).
    Извлекает готовый WireGuard INI из awg.last_config.config.
    """
    data = decode_vpn_uri_to_json(uri)
    if data is None:
        logger.error("build_conf_file: failed to decode vpn:// URI")
        return None
    
    try:
        containers = data.get("containers", [])
        if not containers:
            logger.error("build_conf_file: 'containers' array is empty or missing")
            return None
        
        if not isinstance(containers, list):
            logger.error(
                f"build_conf_file: 'containers' is not a list, "
                f"got {type(containers).__name__}"
            )
            return None
        
        awg = containers[0].get("awg", {})
        if not isinstance(awg, dict):
            logger.error("build_conf_file: 'awg' section is not a dict")
            return None
        
        last_config_str = awg.get("last_config")
        if not last_config_str:
            logger.error("build_conf_file: 'last_config' is missing in awg section")
            return None
        
        if not isinstance(last_config_str, str):
            logger.error(
                f"build_conf_file: 'last_config' is not a string, "
                f"got {type(last_config_str).__name__}"
            )
            return None
        
        try:
            last_config = json.loads(last_config_str)
        except json.JSONDecodeError as e:
            logger.error(
                f"build_conf_file: failed to parse 'last_config' JSON: {e}. "
                f"First 200 chars: {last_config_str[:200]}"
            )
            return None
        
        if not isinstance(last_config, dict):
            logger.error(
                f"build_conf_file: parsed 'last_config' is not a dict, "
                f"got {type(last_config).__name__}"
            )
            return None
        
        config_str = last_config.get("config")
        if not config_str:
            logger.error(
                "build_conf_file: 'config' field is missing or empty in last_config"
            )
            return None
        
        if not isinstance(config_str, str):
            logger.error(
                f"build_conf_file: 'config' is not a string, "
                f"got {type(config_str).__name__}"
            )
            return None
        
        return config_str
    
    except Exception as e:
        logger.error(f"build_conf_file: unexpected error: {e}", exc_info=True)
        return None


def is_valid_vpn_uri(uri: str) -> bool:
    """
    Проверяет валидность vpn:// URI.
    
    🔥 УПРОЩЕНО в соответствии с Вариантом A:
    - Принимает ТОЛЬКО amneziawg2 (protocol_version == "2")
    - Убрана жёсткая валидация параметров AWG 2.0 (S4, S3, Jc, Jmax, H1-H4)
    - Полное доверие API: "Боту нужно просто отдавать то, что пришло из API"
    
    Args:
        uri: Строка вида "vpn://..."
    
    Returns:
        True если URI валидный и содержит AWG 2.0 конфиг, False иначе
    """
    data = decode_vpn_uri_to_json(uri)
    if not data or not isinstance(data, dict):
        return False
    
    containers = data.get("containers")
    if not containers or not isinstance(containers, list):
        return False
    
    for container in containers:
        if not isinstance(container, dict):
            continue
        awg = container.get("awg")
        if not awg or not isinstance(awg, dict):
            continue
        protocol_version = awg.get("protocol_version")
        if str(protocol_version) == "2":
            return True
    
    return False