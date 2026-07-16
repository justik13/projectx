from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)

# Кэш инстансов Fernet для оптимизации CPU.
# 🔥 ИСПРАВЛЕНО #4: Комментарий о том, что это не критично.
# Количество ключей ограничено (обычно 1 DB_ENCRYPTION_KEY).
# Даже при 10 ключах это 10 Fernet объектов в памяти — пренебрежимо мало.
# Cleanup не требуется, но для консистентности с другими файлами добавлен maxsize.
_fernet_cache: dict[str, Fernet] = {}
_FERNET_CACHE_MAX_SIZE = 10  # Обычно 1 ключ, 10 — с запасом


def _get_fernet(key: str) -> Fernet:
    if key not in _fernet_cache:
        # 🔥 ИСПРАВЛЕНО #4: Защита от бесконечного роста (на случай если ключей > 10)
        if len(_fernet_cache) >= _FERNET_CACHE_MAX_SIZE:
            # Удаляем самый старый (first-in) ключ
            oldest_key = next(iter(_fernet_cache))
            del _fernet_cache[oldest_key]
            logger.debug(f"Fernet cache full, evicted oldest key")
        _fernet_cache[key] = Fernet(key.encode("utf-8"))
    return _fernet_cache[key]


class EncryptedString(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        if not key:
            logger.warning("DB_ENCRYPTION_KEY не найден, данные не будут зашифрованы")
            return value
        try:
            f = _get_fernet(key)
            encrypted = f.encrypt(value.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Ошибка шифрования: {e}")
            return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        if not key:
            logger.warning("DB_ENCRYPTION_KEY не найден, данные не будут расшифрованы")
            return value
        try:
            f = _get_fernet(key)
            decrypted = f.decrypt(value.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            logger.warning("Ошибка расшифровки: неверный токен")
            return None
        except Exception as e:
            logger.error(f"Ошибка расшифровки: {e}")
            return None


def encrypt_value(value: str, key: str) -> str:
    if not key:
        return value
    try:
        f = _get_fernet(key)
        encrypted = f.encrypt(value.encode("utf-8"))
        return encrypted.decode("utf-8")
    except Exception:
        return value


def decrypt_value(value: str, key: str) -> str:
    if not key:
        return value
    try:
        f = _get_fernet(key)
        decrypted = f.decrypt(value.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        return value
    except Exception:
        return value