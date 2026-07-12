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
    # 🔧 НОВОЕ: Состояния для редактирования и удаления сервера
    confirming_server_delete = State()
    editing_server_flag = State()
    # tariff creation REMOVED — tariffs are hardcoded
    editing_tariff_days = State()
    editing_tariff_device_limit = State()
    editing_tariff_rub = State()
    editing_tariff_stars = State()