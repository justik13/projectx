"""
Билдер .conf файла для Amnezia.

🔥 ГЛАВНОЕ: .conf файл содержит ВЕСЬ JSON из vpn:// URI
с красивым форматированием (indent=2). Это формат, который
использует Amnezia-клиент для импорта.

НЕ WireGuard INI-формат, а полный JSON со всеми параметрами.
"""
import json
import logging
from typing import Optional
from utils.vpn_parser import (
    AmneziaWGConfig,
    is_valid_amneziawg_config,
    build_conf_content,
    decode_vpn_uri_to_json,
)

logger = logging.getLogger(__name__)


def build_amneziawg_config(config: AmneziaWGConfig) -> Optional[str]:
    """
    🔥 ГЛАВНАЯ ФУНКЦИЯ: возвращает ВЕСЬ JSON из raw_config как строку.
    Если передан AmneziaWGConfig — берём его raw_config (это vpn:// URI).
    """
    if config is None:
        return None

    # Если у нас есть оригинальный vpn:// URI — используем его
    if config.raw_config and config.raw_config.startswith("vpn://"):
        return build_conf_content(config.raw_config)

    # Fallback: если raw_config нет, но есть валидный объект
    if not is_valid_amneziawg_config(config):
        logger.warning("build_amneziawg_config: недостаточно данных")
        return None

    # Крайний fallback: собираем минимальный JSON вручную
    fallback_data = {
        "containers": [
            {
                "awg": {
                    "protocol_version": config.protocol_version or "2",
                },
                "container": "amnezia-awg2",
            }
        ],
        "defaultContainer": "amnezia-awg2",
        "description": config.description or "",
        "dns1": "1.1.1.1",
        "dns2": "1.0.0.1",
        "hostName": config.host_name or "",
    }
    return json.dumps(fallback_data, indent=2, ensure_ascii=False) + "\n"


def build_amneziawg_config_from_uri(vpn_uri: str) -> Optional[str]:
    """
    High-level: парсит vpn:// URI и возвращает .conf содержимое.
    Возвращает ВЕСЬ JSON с indent=2.
    """
    return build_conf_content(vpn_uri)