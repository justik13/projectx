import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class AmneziaWGConfig:
    """Распарсенная конфигурация AmneziaWG"""
    # Протокол: amneziawg или amneziawg2
    protocol: str = "amneziawg2"
    
    # Базовые WireGuard параметры (извлекаются из config)
    address: str = ""
    private_key: str = ""
    dns: str = ""
    
    # Peer параметры
    peer_public_key: str = ""
    peer_preshared_key: str = ""
    peer_allowed_ips: str = "0.0.0.0/0, ::/0"
    peer_endpoint: str = ""
    peer_persistent_keepalive: int = 25
    
    # Обфускация — общая для v1 и v2
    H1: int = 0
    H2: int = 0
    H3: int = 0
    H4: int = 0
    S1: int = 0
    S2: int = 0
    
    # Обфускация — только v1
    J1: int = 0
    J2: int = 0
    J3: int = 0
    
    # Обфускация — только v2 (AmneziaWG 2.0)
    Jc: int = 0
    Jmin: int = 0
    Jmax: int = 0
    
    # Метаданные
    description: str = ""
    host_name: str = ""
    
    # Сырой конфиг (fallback)
    raw_config: str = ""
    
    # Все параметры в dict (для отладки)
    extra: Dict[str, Any] = field(default_factory=dict)


def parse_vpn_uri(uri: str) -> Optional[AmneziaWGConfig]:
    """
    Парсит vpn:// URI и возвращает структуру AmneziaWGConfig.
    
    Returns:
        AmneziaWGConfig при успехе, None при ошибке парсинга.
    """
    if not uri or not isinstance(uri, str):
        logger.warning("parse_vpn_uri: пустой или не-str вход")
        return None
    
    # Убираем префикс vpn://
    if uri.startswith("vpn://"):
        payload = uri[6:]
    else:
        logger.warning(f"parse_vpn_uri: URI не начинается с vpn://, получено: {uri[:30]}...")
        return None
    
    if not payload:
        logger.warning("parse_vpn_uri: пустой payload после vpn://")
        return None
    
    # Пробуем декодировать base64 (с padding и без)
    decoded_bytes = None
    
    # Попытка 1: standard base64
    try:
        decoded_bytes = base64.b64decode(payload, validate=False)
    except Exception:
        pass
    
    # Попытка 2: urlsafe base64
    if decoded_bytes is None:
        try:
            # Добавляем padding если нужно
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
    
    if decoded_bytes is None:
        logger.warning("parse_vpn_uri: не удалось декодировать base64")
        return None
    
    # Парсим JSON
    try:
        data = json.loads(decoded_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning(f"parse_vpn_uri: ошибка декодирования JSON: {e}")
        return None
    
    if not isinstance(data, dict):
        logger.warning("parse_vpn_uri: JSON не dict")
        return None
    
    return _extract_config(data, original_uri=uri)


def _extract_config(data: dict, original_uri: str) -> Optional[AmneziaWGConfig]:
    """Извлекает конфигурацию из распарсенного JSON."""
    config = AmneziaWGConfig(raw_config=original_uri)
    
    # Метаданные верхнего уровня
    config.description = data.get("description", "") or ""
    config.host_name = data.get("hostName", "") or ""
    dns1 = data.get("dns1") or "8.8.8.8"
    dns2 = data.get("dns2") or "8.8.4.4"
    config.dns = f"{dns1}, {dns2}"
    
    # Ищем контейнер с конфигом
    containers = data.get("containers", [])
    if not containers or not isinstance(containers, list):
        logger.warning("_extract_config: нет containers")
        return None
    
    # Берём первый контейнер
    container = containers[0]
    if not isinstance(container, dict):
        return None
    
    # Ищем секцию с протоколом: amneziawg2 / amneziawg / awg
    awg_section = None
    protocol_found = "amneziawg2"
    
    for proto_key in ("amneziawg2", "amneziawg", "awg"):
        if proto_key in container and isinstance(container[proto_key], dict):
            awg_section = container[proto_key]
            protocol_found = proto_key
            break
    
    if awg_section is None:
        logger.warning("_extract_config: не найдена секция awg/amneziawg/amneziawg2")
        return None
    
    config.protocol = protocol_found
    
    # Базовый WG-конфиг (строка внутри секции)
    base_config = awg_section.get("config", "") or ""
    
    # Парсим базовый WG-конфиг
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
    
    # Сохраняем все остальные параметры для отладки
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
                # Перезаписываем DNS из базового конфига (он приоритетнее)
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
    """Проверяет, достаточно ли данных для сборки валидного .conf"""
    if config is None:
        return False
    
    # Минимально необходимые поля
    required = [
        config.private_key,
        config.peer_public_key,
        config.peer_endpoint,
    ]
    
    if not all(required):
        return False
    
    # Для amneziawg2 нужны Jc, Jmin, Jmax
    if config.protocol == "amneziawg2":
        if config.Jc == 0 and config.Jmin == 0 and config.Jmax == 0:
            # Возможно, это v1 конфиг — проверим H1-H4
            if all(x == 0 for x in (config.H1, config.H2, config.H3, config.H4)):
                return False
    
    return True
