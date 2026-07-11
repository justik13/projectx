from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id
from services.subscription import SubscriptionService
from bot.texts import WELCOME_TEXT, HELP_TEXT
from bot.keyboards.common import get_main_menu, get_help_keyboard
from config.settings import get_settings
import logging
import re
from database.models import User

router = Router()


def parse_referral_id(command_args: str) -> int | None:
    if not command_args:
        return None
    match = re.match(r"ref_(\d+)", command_args)
    if match:
        return int(match.group(1))
    return None


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: Command,
    session: AsyncSession  # ← Инжектируется через DBSessionMiddleware
):
    await state.clear()
    telegram_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    ref_id = parse_referral_id(command.args) if command.args else None

    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        user = await SubscriptionService.process_onboarding(
            session, telegram_id, username, first_name, ref_id
        )
        logging.info(f"New user created: {telegram_id} (referred by {ref_id})")

    settings = get_settings()
    is_admin = telegram_id in settings.ADMIN_IDS
    await message.answer(
        WELCOME_TEXT,
        reply_markup=get_main_menu(is_admin=is_admin),
        parse_mode="HTML"
    )


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        HELP_TEXT,
        reply_markup=get_help_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer("🏠 Главное меню")