# utils/encryption.py
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)

# 🔥 Кэш инстансов Fernet для оптимизации CPU
_fernet_cache: dict[str, Fernet] = {}

def _get_fernet(key: str) -> Fernet:
    if key not in _fernet_cache:
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