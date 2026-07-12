import logging
import re
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards.common import get_help_keyboard, get_hub_keyboard
from bot import texts
from config.settings import get_settings
from database.models import User
from database.repositories.users_repo import get_user_by_telegram_id
from services.subscription import SubscriptionService
from utils.telegram import safe, safe_delete_message

router = Router()
logger = logging.getLogger(__name__)

def parse_referral_id(command_args: str) -> int | None:
    if not command_args: return None
    match = re.match(r"ref_(\d+)", command_args)
    return int(match.group(1)) if match else None

async def render_hub(target, user: User, session: AsyncSession, *, edit: bool):
    is_active = await SubscriptionService.check_access(session, user.telegram_id)
    is_admin = user.telegram_id in get_settings().ADMIN_IDS
    name = safe(user.first_name or "Пользователь")
    text = texts.HUB_HEADER.format(name=name)
    kb = get_hub_keyboard(is_admin=is_admin, is_active=is_active)
    
    if edit:
        try: await target.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest: pass
    else:
        await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: Command, session: AsyncSession):
    await state.clear()
    telegram_id = message.from_user.id
    ref_id = parse_referral_id(command.args) if command.args else None
    user = await get_user_by_telegram_id(session, telegram_id)
    
    if not user:
        await SubscriptionService.process_onboarding(session, telegram_id, message.from_user.username, message.from_user.first_name, ref_id)
        logger.info(f"New user created: {telegram_id} (referred by {ref_id})")
        user = await get_user_by_telegram_id(session, telegram_id)
    
    await render_hub(message, user, session, edit=False)

@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.HELP_TEXT, reply_markup=get_help_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await render_hub(callback.message, db_user, session, edit=True)
    await callback.answer()