from .common import get_hub_keyboard, get_back_button
from .user import get_profile_keyboard, get_history_keyboard, get_referral_keyboard
from .device import get_device_keyboard, get_device_delete_confirm_keyboard
from .payment import (
    get_tariff_showcase_keyboard, get_tariff_duration_keyboard,
    get_renew_keyboard, get_change_tariff_keyboard,
    get_payment_method_keyboard, get_payment_success_keyboard,
    get_sbp_payment_keyboard,
)
from .admin.dashboard import get_admin_menu, get_audit_keyboard
from .admin.users import get_admin_user_card_keyboard
from .admin.servers import get_admin_server_card_keyboard, get_server_delete_confirm_keyboard
from .admin.tariffs import get_admin_tariff_card_keyboard
from .admin.broadcast import get_broadcast_confirm_keyboard

__all__ = [
    "get_hub_keyboard", "get_back_button",
    "get_profile_keyboard", "get_history_keyboard", "get_referral_keyboard",
    "get_device_keyboard", "get_device_delete_confirm_keyboard",
    "get_tariff_showcase_keyboard", "get_tariff_duration_keyboard",
    "get_renew_keyboard", "get_change_tariff_keyboard",
    "get_payment_method_keyboard", "get_payment_success_keyboard",
    "get_sbp_payment_keyboard",
    "get_admin_menu", "get_audit_keyboard",
    "get_admin_user_card_keyboard",
    "get_admin_server_card_keyboard", "get_server_delete_confirm_keyboard",
    "get_admin_tariff_card_keyboard",
    "get_broadcast_confirm_keyboard",
]