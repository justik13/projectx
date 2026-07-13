"""Unit тесты для клавиатур."""
import pytest


class TestKeyboards:
    @pytest.mark.unit
    def test_get_hub_keyboard_user(self):
        from bot.keyboards import get_hub_keyboard
        
        kb = get_hub_keyboard(is_admin=False, is_active=False)
        assert kb is not None
        assert len(kb.inline_keyboard) > 0

    @pytest.mark.unit
    def test_get_hub_keyboard_admin(self):
        from bot.keyboards import get_hub_keyboard
        
        kb = get_hub_keyboard(is_admin=True, is_active=True)
        assert kb is not None
        
        # Должна быть кнопка "Админка"
        has_admin_button = False
        for row in kb.inline_keyboard:
            for btn in row:
                if "Админка" in (btn.text or ""):
                    has_admin_button = True
        assert has_admin_button

    @pytest.mark.unit
    def test_get_back_button(self):
        from bot.keyboards import get_back_button
        
        kb = get_back_button("test_callback")
        assert kb is not None
        assert len(kb.inline_keyboard) == 1
        assert kb.inline_keyboard[0][0].callback_data == "test_callback"

    @pytest.mark.unit
    def test_get_back_button_default(self):
        from bot.keyboards import get_back_button
        
        kb = get_back_button()
        assert kb.inline_keyboard[0][0].callback_data == "back_to_main_menu"

    @pytest.mark.unit
    def test_get_profile_keyboard_active(self):
        from bot.keyboards import get_profile_keyboard
        
        kb = get_profile_keyboard(is_active=True)
        assert kb is not None

    @pytest.mark.unit
    def test_get_device_keyboard(self):
        from bot.keyboards import get_device_keyboard
        
        kb = get_device_keyboard(profile_id=123)
        assert kb is not None
        
        # Проверяем что есть callback_data с ID
        has_profile_id = False
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data and "123" in btn.callback_data:
                    has_profile_id = True
        assert has_profile_id

    @pytest.mark.unit
    def test_get_tariff_showcase_keyboard(self):
        from bot.keyboards import get_tariff_showcase_keyboard
        
        grouped = {
            2: ["tariff1", "tariff2"],
            5: ["tariff3"],
        }
        kb = get_tariff_showcase_keyboard(grouped)
        assert kb is not None

    @pytest.mark.unit
    def test_get_payment_method_keyboard(self):
        from bot.keyboards import get_payment_method_keyboard
        
        kb = get_payment_method_keyboard(tariff_id=1, device_limit=2)
        assert kb is not None

    @pytest.mark.unit
    def test_get_admin_menu(self):
        from bot.keyboards import get_admin_menu
        
        kb = get_admin_menu()
        assert kb is not None
        
        # Проверяем наличие основных разделов
        all_text = ""
        for row in kb.inline_keyboard:
            for btn in row:
                all_text += (btn.text or "") + " "
        
        assert "Пользователи" in all_text
        assert "Серверы" in all_text
        assert "Тарифы" in all_text

    @pytest.mark.unit
    def test_get_admin_server_card_keyboard(self):
        from bot.keyboards import get_admin_server_card_keyboard
        
        kb_active = get_admin_server_card_keyboard(server_id=1, is_active=True)
        kb_inactive = get_admin_server_card_keyboard(server_id=1, is_active=False)
        
        assert kb_active is not None
        assert kb_inactive is not None

    @pytest.mark.unit
    def test_get_broadcast_confirm_keyboard(self):
        from bot.keyboards import get_broadcast_confirm_keyboard
        
        kb = get_broadcast_confirm_keyboard()
        assert kb is not None

    @pytest.mark.unit
    def test_get_referral_keyboard(self):
        from bot.keyboards import get_referral_keyboard
        
        kb = get_referral_keyboard("https://t.me/test?start=ref_123")
        assert kb is not None

    @pytest.mark.unit
    def test_get_history_keyboard(self):
        from bot.keyboards import get_history_keyboard
        
        kb = get_history_keyboard()
        assert kb is not None
