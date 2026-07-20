import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text

from config.settings import get_settings

logger = logging.getLogger(__name__)

_fernet_cache: dict[str, Fernet] = {}
_FERNET_CACHE_MAX_SIZE = 10


def _get_fernet(key: str) -> Fernet:
    if key not in _fernet_cache:
        if len(_fernet_cache) >= _FERNET_CACHE_MAX_SIZE:
            oldest_key = next(iter(_fernet_cache))
            del _fernet_cache[oldest_key]
            logger.debug("Fernet cache full, evicted oldest key")

        _fernet_cache[key] = Fernet(key.encode("utf-8"))

    return _fernet_cache[key]


class EncryptedString(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, critical: bool = False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.critical = critical

    def process_bind_param(self, value, dialect):
        if value is None:
            return None

        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY

        if not key:
            raise RuntimeError(
                "CRITICAL: DB_ENCRYPTION_KEY is empty! "
                "Cannot write sensitive data in plaintext. "
                "Fix .env immediately."
            )

        try:
            f = _get_fernet(key)
            encrypted = f.encrypt(value.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logger.error("Encryption failed: %s", type(e).__name__)
            raise RuntimeError("Encryption failed")

    def process_result_value(self, value, dialect):
        if value is None:
            return None

        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY

        if not key:
            if self.critical:
                logger.critical(
                    "DB_ENCRYPTION_KEY is empty during decryption of a critical field. "
                    "Returning None, but this must be treated as a security incident."
                )
            else:
                logger.error(
                    "DB_ENCRYPTION_KEY is empty during decryption. Returning None."
                )
            return None

        try:
            f = _get_fernet(key)
            decrypted = f.decrypt(value.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            if self.critical:
                logger.critical(
                    "Critical encrypted field decryption failed: invalid token. "
                    "Possible causes: DB_ENCRYPTION_KEY was changed, data is corrupted, "
                    "or value was stored in plaintext. Returning None."
                )
            else:
                logger.warning(
                    "Encrypted field decryption failed: invalid token. "
                    "Possible causes: DB_ENCRYPTION_KEY was changed, data is corrupted, "
                    "or value was stored in plaintext. Returning None."
                )
            return None
        except Exception as e:
            if self.critical:
                logger.critical(
                    "Critical encrypted field decryption failed: %s",
                    type(e).__name__,
                )
            else:
                logger.error(
                    "Encrypted field decryption failed: %s",
                    type(e).__name__,
                )
            return None


def encrypt_value(value: str, key: str) -> str:
    if not key:
        raise RuntimeError("Cannot encrypt without key")

    try:
        f = _get_fernet(key)
        encrypted = f.encrypt(value.encode("utf-8"))
        return encrypted.decode("utf-8")
    except Exception as e:
        raise RuntimeError("Encryption failed") from e


def decrypt_value(value: str, key: str) -> str | None:
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


def require_decrypted(value: str | None, field_name: str) -> str:
    """
    Использовать в критичных сервисах после чтения EncryptedString.

    Пример:
        api_key = require_decrypted(server.api_key, "Server.api_key")

    Если значение None, значит:
    - ключ шифрования пуст;
    - ключ шифрования изменился;
    - данные повреждены;
    - значение было записано некорректно.

    В этом случае лучше поднять явную ошибку, чем тихо продолжать работу
    с пустым секретом.
    """
    if value is None:
        raise RuntimeError(
            f"Critical encrypted field is unavailable: {field_name}. "
            "Check DB_ENCRYPTION_KEY and database integrity."
        )

    return value