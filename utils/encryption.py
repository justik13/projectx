from cryptography.fernet import Fernet, InvalidToken
from typing import Optional

def encrypt_value(value: str, key: str) -> str:
    """Шифрует значение с использованием ключа"""
    if not key:
        return value
    
    try:
        f = Fernet(key.encode("utf-8"))
        encrypted = f.encrypt(value.encode("utf-8"))
        return encrypted.decode("utf-8")
    except Exception:
        return value

def decrypt_value(value: str, key: str) -> str:
    """Расшифровывает значение с использованием ключа"""
    if not key:
        return value
    
    try:
        f = Fernet(key.encode("utf-8"))
        decrypted = f.decrypt(value.encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        # Если расшифровка не удалась, возвращаем исходное значение
        return value
    except Exception:
        return value
