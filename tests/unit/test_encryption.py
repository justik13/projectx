"""Unit тесты для модуля шифрования."""
import pytest
from cryptography.fernet import Fernet


class TestEncryption:
    @pytest.mark.unit
    def test_encrypt_decrypt_value(self):
        from utils.encryption import encrypt_value, decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        original = "test_secret_data_123"
        
        encrypted = encrypt_value(original, key)
        assert encrypted != original
        
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == original
    
    @pytest.mark.unit
    def test_encrypt_empty_value(self):
        from utils.encryption import encrypt_value, decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        encrypted = encrypt_value("", key)
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == ""
    
    @pytest.mark.unit
    def test_encrypt_unicode(self):
        from utils.encryption import encrypt_value, decrypt_value
        
        key = Fernet.generate_key().decode("utf-8")
        original = "тест_🚀"
        
        encrypted = encrypt_value(original, key)
        decrypted = decrypt_value(encrypted, key)
        assert decrypted == original
