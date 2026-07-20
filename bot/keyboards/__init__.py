from .common import get_hub_keyboard, get_back_button
from .user import get_profile_keyboard, get_history_keyboard, get_referral_keyboard
from .device import get_device_keyboard, get_device_delete_confirm_keyboard
from .payment import (
    get_tariff_showcase_keyboard,
    get_tariff_duration_keyboard,
    get_renew_keyboard,
    get_change_tariff_keyboard,
    get_payment_method_keyboard,
    get_payment_success_keyboard,
    get_sbp_payment_keyboard,
)
from .admin.dashboard import (
    get_admin_menu,
    get_audit_keyboard,
    get_maintenance_confirm_keyboard,
)
from .admin.users import (
    get_admin_user_card_keyboard,
    get_admin_subscription_keyboard,
    get_admin_change_tariff_keyboard,
    get_admin_grant_tariff_keyboard,
    get_admin_grant_days_keyboard,
    get_admin_extend_days_new_keyboard,
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
)
from .admin.servers import (
    get_admin_server_card_keyboard,
    get_server_delete_confirm_keyboard,
)
from .admin.tariffs import get_admin_tariff_card_keyboard
from .admin.broadcast import (
    get_broadcast_confirm_keyboard,
    get_broadcast_result_keyboard,
    get_broadcast_close_keyboard,
)

__all__ = [
    # common
    "get_hub_keyboard",
    "get_back_button",
    # user
    "get_profile_keyboard",
    "get_history_keyboard",
    "get_referral_keyboard",
    # device
    "get_device_keyboard",
    "get_device_delete_confirm_keyboard",
    # payment
    "get_tariff_showcase_keyboard",
    "get_tariff_duration_keyboard",
    "get_renew_keyboard",
    "get_change_tariff_keyboard",
    "get_payment_method_keyboard",
    "get_payment_success_keyboard",
    "get_sbp_payment_keyboard",
    # admin dashboard
    "get_admin_menu",
    "get_audit_keyboard",
    "get_maintenance_confirm_keyboard",
    # admin users
    "get_admin_user_card_keyboard",
    "get_admin_subscription_keyboard",
    "get_admin_change_tariff_keyboard",
    "get_admin_grant_tariff_keyboard",
    "get_admin_grant_days_keyboard",
    "get_admin_extend_days_new_keyboard",
    "get_admin_confirm_action_keyboard",
    "get_admin_user_devices_keyboard",
    # admin servers
    "get_admin_server_card_keyboard",
    "get_server_delete_confirm_keyboard",
    # admin tariffs
    "get_admin_tariff_card_keyboard",
    # admin broadcast
    "get_broadcast_confirm_keyboard",
    "get_broadcast_result_keyboard",
    "get_broadcast_close_keyboard",
]