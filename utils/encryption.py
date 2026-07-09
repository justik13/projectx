from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TypeDecorator, Text
from typing import Optional
from datetime import datetime
from config.settings import get_settings
import logging

class EncryptedString(TypeDecorator):
    impl = Text
    cache_ok = True
    
    def process_bind_param(self, value, dialect):
        if value is None:
            return None
            
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        
        if not key:
            logging.warning("DB_ENCRYPTION_KEY не найден, данные не будут зашифрованы")
            return value
            
        try:
            f = Fernet(key.encode("utf-8"))
            encrypted = f.encrypt(value.encode("utf-8"))
            return encrypted.decode("utf-8")
        except Exception as e:
            logging.error(f"Ошибка шифрования: {e}")
            return value
    
    def process_result_value(self, value, dialect):
        if value is None:
            return None
            
        settings = get_settings()
        key = settings.DB_ENCRYPTION_KEY
        
        if not key:
            logging.warning("DB_ENCRYPTION_KEY не найден, данные не будут расшифрованы")
            return value
            
        try:
            f = Fernet(key.encode("utf-8"))
            decrypted = f.decrypt(value.encode("utf-8"))
            return decrypted.decode("utf-8")
        except InvalidToken:
            logging.warning("Ошибка расшифровки: неверный токен")
            return None
        except Exception as e:
            logging.error(f"Ошибка расшифровки: {e}")
            return None

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
