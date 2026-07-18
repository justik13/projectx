from aiogram.fsm.state import StatesGroup, State

class DeviceCreationStates(StatesGroup):
    choose_server = State()
    enter_device_name = State()


class DeviceManagementStates(StatesGroup):
    rename_device = State()

class AdminStates(StatesGroup):
    entering_custom_days = State()
    searching_user = State()
    entering_broadcast_message = State()
    confirming_broadcast = State()
    adding_server = State()
    editing_server = State()
    confirming_server_delete = State()
    editing_server_flag = State()
    editing_tariff_days = State()
    editing_tariff_device_limit = State()
    editing_tariff_rub = State()
    editing_tariff_stars = State()
    admin_reducing_days = State()
    admin_extending_custom = State()
    admin_grant_custom_days = State()