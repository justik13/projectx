from aiogram.fsm.state import State, StatesGroup


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
    adding_tariff = State()
    editing_tariff_days = State()
    editing_tariff_device_limit = State()  # ← НОВОЕ
    editing_tariff_rub = State()
    editing_tariff_stars = State()