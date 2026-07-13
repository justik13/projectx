"""Расширенные unit тесты для шифрования."""
import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet, InvalidToken


class TestEncryptionExtended:
    @pytest.mark.unit
    def test_encrypted_string_type(self):
        from utils.encryption import EncryptedString
        
        enc_type = EncryptedString()
        assert enc_type.impl_instance is not None

    @pytest.mark.unit
    def test_encrypt_none_value(self):
        from utils.encryption import encrypt_value
        
        result = encrypt_value(None, "test_key")
        assert result is None

    @pytest.mark.unit
    def test_decrypt_none_value(self):
        from utils.encryption import decrypt_value
        
        result = decrypt_value(None, "test_key")
        assert result is None

    @pytest.mark.unit
    def test_encrypt_empty_key(self):
        from utils.encryption import encrypt_value
        
        # Без ключа — возвращается оригинал
        result = encrypt_value("test_data", "")
        assert result == "test_data"

    @pytest.mark.unit
    def test_decrypt_invalid_token(self):
        from utils.encryption import decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        # Невалидный токен
        result = decrypt_value("invalid_token_data", key)
        assert result == "invalid_token_data"  # Возвращает оригинал при ошибке

    @pytest.mark.unit
    def test_fernet_cache(self):
        from utils.encryption import _get_fernet, _fernet_cache
        
        key = Fernet.generate_key().decode("utf-8")
        
        # Первый вызов — создаёт новый
        f1 = _get_fernet(key)
        
        # Второй вызов — возвращает из кэша
        f2 = _get_fernet(key)
        
        assert f1 is f2  # Тот же объект
        assert key in _fernet_cache

    @pytest.mark.unit
    def test_encrypted_string_process_bind_param(self):
        from utils.encryption import EncryptedString
        
        enc = EncryptedString()
        
        # None значение
        result = enc.process_bind_param(None, None)
        assert result is None

    @pytest.mark.unit
    def test_encrypted_string_process_result_value(self):
        from utils.encryption import EncryptedString
        
        enc = EncryptedString()
        
        # None значение
        result = enc.process_result_value(None, None)
        assert result is None

    @pytest.mark.unit
    def test_encrypt_decrypt_roundtrip_long_text(self):
        from utils.encryption import encrypt_value, decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        original = "A" * 10000  # Длинный текст
        
        encrypted = encrypt_value(original, key)
        decrypted = decrypt_value(encrypted, key)
        
        assert decrypted == original

    @pytest.mark.unit
    def test_encrypt_decrypt_special_chars(self):
        from utils.encryption import encrypt_value, decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        original = "!@#$%^&*()_+-=[]{}|;':\",./<>?"
        
        encrypted = encrypt_value(original, key)
        decrypted = decrypt_value(encrypted, key)
        
        assert decrypted == original
