# bot/states.py — FSM-состояния для бота

from aiogram.fsm.state import State, StatesGroup


class OnboardingStates(StatesGroup):
    """Состояния для онбординга (первый запуск)"""
    waiting_for_tos_accept = State()


class DeviceCreationStates(StatesGroup):
    """Состояния для создания устройства"""
    choose_server = State()
    enter_device_name = State()


class DeviceManagementStates(StatesGroup):
    """Состояния для управления устройством"""
    rename_device = State()


class PaymentStates(StatesGroup):
    """Состояния для оплаты"""
    select_tariff = State()
    select_payment_method = State()
    confirm_payment = State()


class AdminStates(StatesGroup):
    """Состояния для админки"""
    # Пользователи
    viewing_users_list = State()
    viewing_user_card = State()
    extending_subscription = State()
    entering_custom_days = State()
    searching_user = State()
    
    # Рассылка
    entering_broadcast_message = State()
    confirming_broadcast = State()
    
    # Серверы
    viewing_servers_list = State()
    viewing_server_card = State()
    adding_server = State()
    editing_server = State()
    
    # Тарифы
    viewing_tariffs_list = State()
    viewing_tariff_card = State()
    adding_tariff = State()
    editing_tariff = State()
    editing_tariff_days = State()
    editing_tariff_rub = State()
    editing_tariff_stars = State()
