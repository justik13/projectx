from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from services.subscription import SubscriptionService
from bot.texts import WELCOME_TEXT, TOS_TEXT, TOS_ACCEPT_PROMPT
from bot.keyboards import get_main_menu, get_tos_keyboard
from config.settings import get_settings
import logging
import re

router = Router()

# Импортируем middleware
from bot.middlewares import BanCheckMiddleware

# Регистрируем middleware для проверки бана
router.message.middleware(BanCheckMiddleware())
router.callback_query.middleware(BanCheckMiddleware())

def parse_referral_id(command_args: str) -> int | None:
    """Парсит реферальный ID из аргументов /start"""
    match = re.match(r"ref_(\d+)", command_args)
    if match:
        return int(match.group(1))
    return None

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: Command):
    """Обработчик /start с поддержкой реферальной ссылки"""
    
    # Очищаем FSM состояние
    await state.clear()
    
    telegram_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    
    # Парсим реферальный ID из аргументов
    ref_id = None
    if command.args:
        ref_id = parse_referral_id(command.args)
    
    # Получаем сессию БД
    session = await get_session()
    
    try:
        # Проверяем, существует ли пользователь
        user = await get_user_by_telegram_id(session, telegram_id)
        
        if not user:
            # Создаём нового пользователя с учётом реферала
            user = await SubscriptionService.process_onboarding(
                session, telegram_id, username, first_name, ref_id
            )
            logging.info(f"New user created: {telegram_id} (referred by {ref_id})")
        
        # Проверяем, принял ли пользователь оферту
        if not user.tos_accepted:
            await message.answer(
                TOS_ACCEPT_PROMPT,
                reply_markup=get_tos_keyboard()
            )
            return
        
        # Показываем главное меню
        settings = get_settings()
        is_admin = telegram_id in settings.ADMIN_IDS
        await message.answer(
            WELCOME_TEXT,
            reply_markup=get_main_menu(is_admin=is_admin)
        )
    
    finally:
        await session.close()

@router.callback_query(F.data == "accept_tos")
async def accept_tos(callback: CallbackQuery, state: FSMContext):
    """Обработчик принятия оферты"""
    
    telegram_id = callback.from_user.id
    session = await get_session()
    
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if user:
            await update_user(session, user, tos_accepted=True)
            logging.info(f"User {telegram_id} accepted ToS")
        
        # Показываем главное меню
        settings = get_settings()
        is_admin = telegram_id in settings.ADMIN_IDS
        await callback.message.edit_text(WELCOME_TEXT)
        await callback.message.answer(
            "✅ Добро пожаловать!",
            reply_markup=get_main_menu(is_admin=is_admin)
        )
        
        await callback.answer()
    
    finally:
        await session.close()

@router.callback_query(F.data == "read_tos")
async def read_tos(callback: CallbackQuery):
    """Обработчик чтения оферты"""
    await callback.message.edit_text(
        TOS_TEXT,
        reply_markup=get_tos_keyboard()
    )
    await callback.answer()

@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    """Заглушка для раздела Профиль (будет реализовано позже)"""
    await message.answer("👤 Раздел 'Профиль' находится в разработке.")

@router.message(F.text == "🔌 Подключение")
async def show_connection(message: Message):
    """Заглушка для раздела Подключение (будет реализовано позже)"""
    await message.answer("🔌 Раздел 'Подключение' находится в разработке.")

@router.message(F.text == "💳 Оплата")
async def show_payment(message: Message):
    """Заглушка для раздела Оплата (будет реализовано позже)"""
    await message.answer("💳 Раздел 'Оплата' находится в разработке.")

@router.message(F.text == "💬 Поддержка")
async def show_support(message: Message):
    """Заглушка для раздела Поддержка (будет реализовано позже)"""
    settings = get_settings()
    await message.answer(f"💬 Поддержка: {settings.SUPPORT_USERNAME}")

@router.message(F.text == "🛠 Админка")
async def show_admin(message: Message):
    """Заглушка для админки (будет реализовано позже)"""
    settings = get_settings()
    if message.from_user.id not in settings.ADMIN_IDS:
        await message.answer("⛔️ У вас нет доступа к админ-панели.")
        return
    await message.answer("🛠 Админ-панель находится в разработке.")
