"""Расширенные unit тесты для keyboards."""
import pytest
from unittest.mock import MagicMock


class TestKeyboardsExtended:
    @pytest.mark.unit
    def test_get_hub_keyboard_inactive_user(self):
        from bot.keyboards import get_hub_keyboard

        kb = get_hub_keyboard(is_admin=False, is_active=False)
        assert kb is not None

        has_buy_button = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Купить" in (btn.text or ""):
                    has_buy_button = True
        assert has_buy_button

    @pytest.mark.unit
    def test_get_hub_keyboard_active_user(self):
        from bot.keyboards import get_hub_keyboard

        kb = get_hub_keyboard(is_admin=False, is_active=True)
        assert kb is not None

        has_subscription_button = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "подписка" in (btn.text or "").lower():
                    has_subscription_button = True
        assert has_subscription_button

    @pytest.mark.unit
    def test_get_back_button_custom(self):
        from bot.keyboards import get_back_button

        kb = get_back_button("custom_callback")
        assert kb is not None
        assert kb.inline_keyboard[0][0].callback_data == "custom_callback"

    @pytest.mark.unit
    def test_get_profile_keyboard_inactive(self):
        from bot.keyboards import get_profile_keyboard

        kb = get_profile_keyboard(is_active=False)
        assert kb is not None

    @pytest.mark.unit
    def test_get_device_delete_confirm_keyboard(self):
        from bot.keyboards import get_device_delete_confirm_keyboard

        kb = get_device_delete_confirm_keyboard(profile_id=123)
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("Да" in t or "✅" in t for t in texts)
        assert any("Отмена" in t or "❌" in t for t in texts)

    @pytest.mark.unit
    def test_get_tariff_duration_keyboard(self):
        from bot.keyboards import get_tariff_duration_keyboard
        from database.models import Tariff

        tariff1 = Tariff(duration_days=7, device_limit=2, price_rub=50, price_stars=50)
        tariff2 = Tariff(duration_days=30, device_limit=2, price_rub=100, price_stars=100)

        kb = get_tariff_duration_keyboard([tariff1, tariff2])
        assert kb is not None
        assert len(kb.inline_keyboard) >= 2

    @pytest.mark.unit
    def test_get_renew_keyboard(self):
        from bot.keyboards import get_renew_keyboard
        from database.models import Tariff

        tariff = Tariff(duration_days=30, device_limit=2, price_rub=100, price_stars=100)

        kb = get_renew_keyboard([tariff])
        assert kb is not None

    @pytest.mark.unit
    def test_get_change_tariff_keyboard(self):
        from bot.keyboards import get_change_tariff_keyboard
        from database.models import Tariff

        tariff1 = Tariff(duration_days=30, device_limit=2, price_rub=100, price_stars=100)
        tariff2 = Tariff(duration_days=30, device_limit=5, price_rub=200, price_stars=200)

        kb = get_change_tariff_keyboard([tariff1, tariff2], current_limit=2, is_subscription_active=True)
        assert kb is not None

    @pytest.mark.unit
    def test_get_payment_method_keyboard_with_device_limit(self):
        from bot.keyboards import get_payment_method_keyboard

        kb = get_payment_method_keyboard(tariff_id=1, device_limit=2)
        assert kb is not None

        has_stars = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Stars" in (btn.text or "") or "⭐" in (btn.text or ""):
                    has_stars = True
        assert has_stars

    @pytest.mark.unit
    def test_get_payment_success_keyboard(self):
        from bot.keyboards import get_payment_success_keyboard

        kb = get_payment_success_keyboard()
        assert kb is not None

        has_connect = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Подключ" in (btn.text or "") or "🔌" in (btn.text or ""):
                    has_connect = True
        assert has_connect

    @pytest.mark.unit
    def test_get_admin_menu(self):
        from bot.keyboards import get_admin_menu

        kb = get_admin_menu()
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("Пользователи" in t or "👥" in t for t in texts)
        assert any("Серверы" in t or "🌍" in t for t in texts)
        assert any("Тарифы" in t or "💰" in t for t in texts)

    @pytest.mark.unit
    def test_get_admin_user_card_keyboard(self):
        from bot.keyboards import get_admin_user_card_keyboard

        kb = get_admin_user_card_keyboard(user_id=123456789)
        assert kb is not None

        has_extend = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Продлить" in (btn.text or "") or "⏰" in (btn.text or ""):
                    has_extend = True
        assert has_extend

    @pytest.mark.unit
    def test_get_admin_extend_days_keyboard(self):
        from bot.keyboards import get_admin_extend_days_keyboard

        kb = get_admin_extend_days_keyboard(user_id=123456789)
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("7" in t for t in texts)
        assert any("30" in t for t in texts)
        assert any("навсегда" in t.lower() or "∞" in t for t in texts)

    @pytest.mark.unit
    def test_get_admin_server_card_keyboard_active(self):
        from bot.keyboards import get_admin_server_card_keyboard

        kb = get_admin_server_card_keyboard(server_id=1, is_active=True)
        assert kb is not None

        has_toggle = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Выключить" in (btn.text or "") or "🔴" in (btn.text or ""):
                    has_toggle = True
        assert has_toggle

    @pytest.mark.unit
    def test_get_admin_server_card_keyboard_inactive(self):
        from bot.keyboards import get_admin_server_card_keyboard

        kb = get_admin_server_card_keyboard(server_id=1, is_active=False)
        assert kb is not None

        has_toggle = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Включить" in (btn.text or "") or "🟢" in (btn.text or ""):
                    has_toggle = True
        assert has_toggle

    @pytest.mark.unit
    def test_get_server_delete_confirm_keyboard(self):
        from bot.keyboards import get_server_delete_confirm_keyboard

        kb = get_server_delete_confirm_keyboard(server_id=1)
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("Да" in t or "✅" in t for t in texts)
        assert any("Отмена" in t or "❌" in t for t in texts)

    @pytest.mark.unit
    def test_get_admin_tariff_card_keyboard(self):
        from bot.keyboards import get_admin_tariff_card_keyboard

        kb = get_admin_tariff_card_keyboard(tariff_id=1, is_active=True)
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("дни" in t.lower() or "дн" in t.lower() for t in texts)
        assert any("устр" in t.lower() for t in texts)
        assert any("₽" in t or "руб" in t.lower() for t in texts)
        assert any("⭐" in t or "stars" in t.lower() for t in texts)

    @pytest.mark.unit
    def test_get_broadcast_confirm_keyboard(self):
        from bot.keyboards import get_broadcast_confirm_keyboard

        kb = get_broadcast_confirm_keyboard()
        assert kb is not None

        texts = []
        for row in kb.inline_keyboard:
            for btn in row:
                texts.append(btn.text)

        assert any("всем" in t.lower() or "все" in t.lower() for t in texts)
        assert any("активн" in t.lower() for t in texts)
        assert any("Отмена" in t or "❌" in t for t in texts)

    @pytest.mark.unit
    def test_get_referral_keyboard(self):
        from bot.keyboards import get_referral_keyboard

        kb = get_referral_keyboard("https://t.me/test?start=ref_123")
        assert kb is not None

        has_copy = False
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.copy_text or "Скопировать" in (btn.text or ""):
                    has_copy = True
        assert has_copy

    @pytest.mark.unit
    def test_get_history_keyboard(self):
        from bot.keyboards import get_history_keyboard

        kb = get_history_keyboard()
        assert kb is not None

        has_back = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "профиль" in (btn.text or "").lower() or "←" in (btn.text or ""):
                    has_back = True
        assert has_back
