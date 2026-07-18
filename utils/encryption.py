from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)

# Кэш инстансов Fernet для оптимизации CPU.
_fernet_cache: dict[str, Fernet] = {}
_FERNET_CACHE_MAX_SIZE = 10

def _get_fernet(key: str) -> Fernet:
    if key not in _fernet_cache:
        if len(_fernet_cache) >= _FERNET_CACHE_MAX_SIZE:
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
        
        # 🔥 ИСПРАВЛЕНО CRITICAL #2: Жесткий Fail-Fast вместо молчаливой записи в plaintext
        if not key:
            raise RuntimeError(
                "CRITICAL: DB_ENCRYPTION_KEY is empty! "
                "Cannot write sensitive data (API keys, configs) in plaintext. "
                "Fix .env immediately."
            )
            
        try:
            f = _get_fernet(key)
            encrypted = f.encrypt(value.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error(f"Ошибка шифрования: {e}")
            raise RuntimeError(f"Encryption failed: {e}")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        
        if not key:
            # Если ключ пропал, мы не можем расшифровать. Возвращаем None.
            logger.error("DB_ENCRYPTION_KEY not found during decryption. Returning None.")
            return None
            
        try:
            f = _get_fernet(key)
            decrypted = f.decrypt(value.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            # Возможно, данные были записаны в plaintext (старая уязвимость) или ключ изменился
            logger.warning("Ошибка расшифровки: неверный токен (возможно, plaintext или смена ключа)")
            return None
        except Exception as e:
            logger.error(f"Ошибка расшифровки: {e}")
            return None

def encrypt_value(value: str, key: str) -> str:
    if not key:
        raise RuntimeError("Cannot encrypt without key")
    try:
        f = _get_fernet(key)
        encrypted = f.encrypt(value.encode("utf-8"))
        return encrypted.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Encryption failed: {e}")

def decrypt_value(value: str, key: str) -> str:
    if not key:
        return None
    try:
        f = _get_fernet(key)
        decrypted = f.decrypt(value.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        return None
    except Exception:
        return None