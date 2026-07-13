"""Unit тесты для utils/admin.py"""
import pytest
from unittest.mock import patch


class TestAdminUtils:
    @pytest.mark.unit
    def test_is_admin_true(self):
        from utils.admin import is_admin
        
        # ADMIN_IDS = [123456789, 987654321] в conftest
        assert is_admin(123456789) is True
        assert is_admin(987654321) is True

    @pytest.mark.unit
    def test_is_admin_false(self):
        from utils.admin import is_admin
        
        assert is_admin(999999999) is False
        assert is_admin(0) is False
        assert is_admin(1) is False

    @pytest.mark.unit
    def test_require_admin(self):
        from utils.admin import require_admin
        
        assert require_admin(123456789) is True
        assert require_admin(999999999) is False
