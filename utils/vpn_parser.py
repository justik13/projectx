import base64
import json
import zlib
import struct
import logging
from typing import Optional

logger = logging.getLogger(__name__)


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


def build_vpn_file_from_dict(data: dict) -> str:
    """
    Сериализует декодированный vpn:// JSON в содержимое .vpn файла.
    """
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def build_conf_file_from_dict(data: dict) -> Optional[str]:
    """
    Извлекает WireGuard INI из декодированного vpn:// JSON.

    Приоритет №1:
    - берёт готовый INI из last_config.config.

    Важно:
    - мы НЕ логируем содержимое last_config, потому что там могут быть
      приватные ключи и другие чувствительные данные.
    """
    try:
        containers = data.get("containers", [])
        if not containers:
            logger.error(
                "build_conf_file_from_dict: 'containers' is empty or missing"
            )
            return None

        if not isinstance(containers, list):
            logger.error(
                f"build_conf_file_from_dict: 'containers' is not a list, "
                f"got {type(containers).__name__}"
            )
            return None

        awg = containers[0].get("awg", {})
        if not isinstance(awg, dict):
            logger.error("build_conf_file_from_dict: 'awg' is not a dict")
            return None

        last_config_str = awg.get("last_config")
        if not last_config_str:
            logger.error(
                "build_conf_file_from_dict: 'last_config' missing in awg"
            )
            return None

        if not isinstance(last_config_str, str):
            logger.error(
                f"build_conf_file_from_dict: 'last_config' is not a string, "
                f"got {type(last_config_str).__name__}"
            )
            return None

        try:
            last_config = json.loads(last_config_str)
        except json.JSONDecodeError as e:
            logger.error(
                "build_conf_file_from_dict: failed to parse "
                f"'last_config' JSON: {e}"
            )
            return None

        if not isinstance(last_config, dict):
            logger.error(
                f"build_conf_file_from_dict: parsed 'last_config' "
                f"is not a dict, got {type(last_config).__name__}"
            )
            return None

        config_str = last_config.get("config")
        if not config_str:
            logger.error(
                "build_conf_file_from_dict: 'config' field missing "
                "or empty in last_config"
            )
            return None

        if not isinstance(config_str, str):
            logger.error(
                f"build_conf_file_from_dict: 'config' is not a string, "
                f"got {type(config_str).__name__}"
            )
            return None

        return config_str

    except Exception as e:
        logger.error(
            f"build_conf_file_from_dict: unexpected error: {e}",
            exc_info=True,
        )
        return None


def build_conf_file(uri: str) -> Optional[str]:
    """
    Декодирует vpn:// URI и возвращает содержимое .conf файла.
    """
    data = decode_vpn_uri_to_json(uri)
    if data is None:
        logger.error("build_conf_file: failed to decode vpn:// URI")
        return None

    return build_conf_file_from_dict(data)


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

        awg = container.get("awg")
        if not awg or not isinstance(awg, dict):
            continue

        protocol_version = awg.get("protocol_version")
        if str(protocol_version) == "2":
            return True

    return False