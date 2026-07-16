"""
Парсер vpn:// URI от Amnezia API.
Формат: base64url(4-byte big-endian original_length + zlib_compressed_JSON)

Главные функции:
- decode_vpn_uri_to_json(uri) -> dict: возвращает весь JSON как словарь
- build_vpn_file(uri) -> str: возвращает готовый .vpn (JSON с отступами)
- build_conf_file(uri) -> str: возвращает готовый .conf (WireGuard INI из last_config)
- validate_awg2_config(data) -> AWG2ValidationResult: валидация правил AWG 2.0
"""

import base64
import json
import zlib
import struct
import logging
from dataclasses import dataclass, field
from typing import Optional, List

logger = logging.getLogger(__name__)


# ============================================================
# ВАЛИДАЦИЯ AMNEZIAWG 2.0
# ============================================================

@dataclass
class AWG2ValidationResult:
    """Результат валидации AmneziaWG 2.0 конфигурации."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_awg2_config(data: dict) -> AWG2ValidationResult:
    """
    Валидирует AmneziaWG 2.0 конфигурацию по правилам из AmneziaWG-Architect.

    Проверяет:
    - protocol_version == "2" (ОБЯЗАТЕЛЬНО для AWG 2.0)
    - S4 <= 32, S3 <= 64
    - S1 + 56 != S2, S2 + 92 != S3
    - Jc >= 4, Jmax > 81
    - H1-H4 — строки формата "min-max" без пересечений

    Args:
        data: Распакованный JSON из vpn:// URI

    Returns:
        AWG2ValidationResult с errors и warnings
    """
    result = AWG2ValidationResult(is_valid=True)

    if not isinstance(data, dict):
        result.is_valid = False
        result.errors.append("Data is not a dictionary")
        return result

    containers = data.get("containers", [])
    if not containers:
        result.is_valid = False
        result.errors.append("Missing 'containers' array")
        return result

    awg = None
    for container in containers:
        if isinstance(container, dict):
            awg = container.get("awg")
            if awg and isinstance(awg, dict):
                break

    if not awg or not isinstance(awg, dict):
        result.is_valid = False
        result.errors.append("Missing 'awg' section in containers")
        return result

    # 🔥 ИСПРАВЛЕНО #13b: protocol_version == "2" теперь ОБЯЗАТЕЛЬНО (ERROR, не warning)
    protocol_version = awg.get("protocol_version", "1")
    if str(protocol_version) != "2":
        result.is_valid = False
        result.errors.append(
            f"protocol_version is '{protocol_version}', expected '2' for AWG 2.0. "
            f"AWG 1.0 and 1.5 are NOT supported."
        )
        # Ранний выход — нет смысла проверять остальные параметры для не-AWG 2.0
        return result

    def _safe_int(key: str, default: int = 0) -> int:
        try:
            return int(awg.get(key, default))
        except (ValueError, TypeError):
            return default

    S1 = _safe_int("S1")
    S2 = _safe_int("S2")
    S3 = _safe_int("S3")
    S4 = _safe_int("S4")
    Jc = _safe_int("Jc")
    Jmax = _safe_int("Jmax")

    # Валидация ограничений параметров (из AmneziaWG-Architect)
    if S4 > 32:
        result.errors.append(f"S4 = {S4} (must be <= 32)")
    if S3 > 64:
        result.errors.append(f"S3 = {S3} (must be <= 64)")
    if S1 + 56 == S2:
        result.errors.append(f"S1 + 56 == S2 ({S1} + 56 = {S2}), must differ")
    if S2 + 92 == S3:
        result.errors.append(f"S2 + 92 == S3 ({S2} + 92 = {S3}), must differ")
    if Jc < 4:
        result.errors.append(f"Jc = {Jc} (must be >= 4)")
    if Jmax <= 81:
        result.errors.append(f"Jmax = {Jmax} (must be > 81)")

    # Валидация H1-H4 диапазонов
    h_ranges = []
    for h_key in ("H1", "H2", "H3", "H4"):
        h_val = awg.get(h_key, "")
        if isinstance(h_val, str) and "-" in h_val:
            try:
                parts = h_val.split("-", 1)
                h_min, h_max = int(parts[0]), int(parts[1])
                if h_min > h_max:
                    result.errors.append(f"{h_key}: min ({h_min}) > max ({h_max})")
                else:
                    h_ranges.append((h_key, h_min, h_max))
            except (ValueError, IndexError):
                result.errors.append(f"{h_key}: invalid range format '{h_val}'")
        else:
            result.errors.append(
                f"{h_key}: must be a 'min-max' string, got '{h_val}'"
            )

    # Проверка пересечений диапазонов
    for i in range(len(h_ranges)):
        for j in range(i + 1, len(h_ranges)):
            k1, min1, max1 = h_ranges[i]
            k2, min2, max2 = h_ranges[j]
            if min1 <= max2 and min2 <= max1:
                result.errors.append(
                    f"{k1} ({min1}-{max1}) overlaps with {k2} ({min2}-{max2})"
                )

    if result.errors:
        result.is_valid = False

    return result


# ============================================================
# DECODING
# ============================================================

def _decode_base64url(payload: str) -> Optional[bytes]:
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


# ============================================================
# BUILDERS
# ============================================================

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

    🔥 ИСПРАВЛЕНО #13: Принимает ТОЛЬКО amneziawg2 (protocol_version == "2").
    AWG 1.0 (amneziawg) и AWG 1.5 НЕ поддерживаются согласно amnezia_docs.md.

    Returns:
        True если URI валидный и содержит AWG 2.0 конфиг, False иначе.
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

        # Ищем секцию awg
        awg = container.get("awg")
        if not awg or not isinstance(awg, dict):
            continue

        # 🔥 ИСПРАВЛЕНО #13: Требуем protocol_version == "2"
        protocol_version = awg.get("protocol_version")
        if str(protocol_version) == "2":
            return True

    return False