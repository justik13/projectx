import logging
import re
import asyncio
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards.common import get_hub_keyboard
from bot import texts
from config.settings import get_settings
from database.models import User
from database.repositories.users_repo import get_user_by_telegram_id
from services.subscription import SubscriptionService
from utils.telegram import safe

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
    try: await message.delete()
    except Exception: pass
    telegram_id = message.from_user.id
    ref_id = parse_referral_id(command.args) if command.args else None
    user = await get_user_by_telegram_id(session, telegram_id)
    is_new = False
    if not user:
        is_new = True
        await SubscriptionService.process_onboarding(
            session, telegram_id, message.from_user.username,
            message.from_user.first_name, ref_id,
        )
        logger.info(f"New user created: {telegram_id} (referred by {ref_id})")
        user = await get_user_by_telegram_id(session, telegram_id)
    if is_new:
        # ToS/Privacy как inline-кнопки при первом /start
        builder = InlineKeyboardBuilder()
        builder.button(text="📄 Условия сервиса", url=texts.TOS_AGREEMENT_URL)
        builder.button(text="🔒 Политика", url=texts.PRIVACY_POLICY_URL)
        builder.adjust(2)
        welcome_msg = await message.answer(texts.WELCOME_TEXT, reply_markup=builder.as_markup(), parse_mode="HTML")
        # Через 8 секунд заменяем welcome на хаб
        await asyncio.sleep(8)
        try:
            await welcome_msg.edit_text(
                texts.HUB_HEADER.format(name=safe(user.first_name or "Пользователь")),
                reply_markup=get_hub_keyboard(
                    is_admin=user.telegram_id in get_settings().ADMIN_IDS,
                    is_active=await SubscriptionService.check_access(session, user.telegram_id)
                ),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    else:
        await render_hub(message, user, session, edit=False)


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await callback.answer()
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await render_hub(callback.message, db_user, session, edit=True)