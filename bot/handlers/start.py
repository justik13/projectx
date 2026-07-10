from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id, update_user
from services.subscription import SubscriptionService
from bot.texts import WELCOME_TEXT, TOS_TEXT, TOS_ACCEPT_PROMPT, HELP_TEXT
from bot.keyboards import get_main_menu, get_tos_keyboard, get_tos_accept_keyboard, get_help_keyboard
from config.settings import get_settings
import logging
import re

router = Router()

def parse_referral_id(command_args: str) -> int | None:
    """Парсит реферальный ID из аргументов /start"""
    if not command_args:
        return None
    match = re.match(r"ref_(\d+)", command_args)
    if match:
        return int(match.group(1))
    return None

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: Command):
    """Обработчик /start с поддержкой реферальной ссылки"""
    await state.clear()
    telegram_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    ref_id = parse_referral_id(command.args) if command.args else None
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            user = await SubscriptionService.process_onboarding(
                session, telegram_id, username, first_name, ref_id
            )
            logging.info(f"New user created: {telegram_id} (referred by {ref_id})")
        if not user.tos_accepted:
            await message.answer(
                TOS_ACCEPT_PROMPT,
                reply_markup=get_tos_accept_keyboard()
            )
            return
        settings = get_settings()
        is_admin = telegram_id in settings.ADMIN_IDS
        # 🔥 UX FIX: Одно сообщение вместо двух
        await message.answer(
            WELCOME_TEXT,
            reply_markup=get_main_menu(is_admin=is_admin)
        )
    finally:
        await session.close()

@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    """Обработчик команды /help"""
    await state.clear()
    await message.answer(
        HELP_TEXT,
        reply_markup=get_help_keyboard(),
        parse_mode="HTML"
    )

@router.callback_query(F.data == "accept_tos")
async def accept_tos(callback: CallbackQuery, state: FSMContext):
    """Обработчик принятия оферты"""
    telegram_id = callback.from_user.id
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if user and not user.tos_accepted:
            await update_user(session, user, tos_accepted=True)
            logging.info(f"User {telegram_id} accepted ToS")
            await callback.answer("✅ Оферта принята!", show_alert=False)
            # 🔥 UX FIX: Удаляем сообщение с офертой и отправляем одно финальное
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            settings = get_settings()
            is_admin = telegram_id in settings.ADMIN_IDS
            await callback.message.answer(
                f"✅ <b>Добро пожаловать!</b>\n\n{WELCOME_TEXT}",
                reply_markup=get_main_menu(is_admin=is_admin),
                parse_mode="HTML"
            )
    finally:
        await session.close()

@router.callback_query(F.data == "read_tos")
async def read_tos(callback: CallbackQuery):
    """Обработчик чтения оферты"""
    await callback.answer()
    try:
        await callback.message.edit_text(
            TOS_TEXT,
            reply_markup=get_tos_keyboard()
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise