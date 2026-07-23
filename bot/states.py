from aiogram.fsm.state import StatesGroup, State


class DeviceCreationStates(StatesGroup):
    choose_server = State()
    enter_device_name = State()


class DeviceManagementStates(StatesGroup):
    rename_device = State()


class AdminStates(StatesGroup):
    searching_user = State()
    entering_broadcast_message = State()
    confirming_broadcast = State()
    adding_server = State()
    editing_server = State()
    confirming_server_delete = State()
    editing_server_flag = State()
    editing_server_url = State()
    editing_server_key = State()
    editing_server_max_clients = State()
    editing_tariff_days = State()
    editing_tariff_device_limit = State()
    editing_tariff_rub = State()
    admin_reducing_days = State()
    admin_extending_custom = State()
    admin_grant_custom_days = State()
