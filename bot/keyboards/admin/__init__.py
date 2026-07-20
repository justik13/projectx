from .dashboard import (
    get_admin_menu,
    get_audit_keyboard,
    get_maintenance_confirm_keyboard,
)
from .users import (
    get_admin_user_card_keyboard,
    get_admin_subscription_keyboard,
    get_admin_change_tariff_keyboard,
    get_admin_grant_tariff_keyboard,
    get_admin_grant_days_keyboard,
    get_admin_extend_days_new_keyboard,
    get_admin_confirm_action_keyboard,
    get_admin_user_devices_keyboard,
)
from .servers import (
    get_admin_server_card_keyboard,
    get_server_delete_confirm_keyboard,
)
from .tariffs import get_admin_tariff_card_keyboard
from .broadcast import (
    get_broadcast_confirm_keyboard,
    get_broadcast_result_keyboard,
    get_broadcast_close_keyboard,
)

__all__ = [
    # dashboard
    "get_admin_menu",
    "get_audit_keyboard",
    "get_maintenance_confirm_keyboard",
    # users
    "get_admin_user_card_keyboard",
    "get_admin_subscription_keyboard",
    "get_admin_change_tariff_keyboard",
    "get_admin_grant_tariff_keyboard",
    "get_admin_grant_days_keyboard",
    "get_admin_extend_days_new_keyboard",
    "get_admin_confirm_action_keyboard",
    "get_admin_user_devices_keyboard",
    # servers
    "get_admin_server_card_keyboard",
    "get_server_delete_confirm_keyboard",
    # tariffs
    "get_admin_tariff_card_keyboard",
    # broadcast
    "get_broadcast_confirm_keyboard",
    "get_broadcast_result_keyboard",
    "get_broadcast_close_keyboard",
]