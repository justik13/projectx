import logging
import re
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards.common import get_hub_keyboard
from bot import texts
from config.settings import get_settings
from database.models import User
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.payments_repo import mark_payment_as_cancelled
from services.subscription import SubscriptionService
from utils.telegram import safe, render_hub, clear_and_delete_hub
router = Router()
logger = logging.getLogger(__name__)
def parse_referral_id(command_args: str) -> int | None:
    if not command_args: return None
    match = re.match(r"ref_(\d+)", command_args)
    return int(match.group(1)) if match else None
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, command: Command, session: AsyncSession):
    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    payment_id = data.get("payment_id")
    # 🔥 ИСПРАВЛЕНО SMH: используем clear_and_delete_hub вместо clear_hub_cache
    # Это удаляет ВСЕ сообщения из кэша, а не просто очищает кэш
    if invoice_message_id or payment_id:
        await clear_and_delete_hub(message.bot, message.chat.id)
    if payment_id:
        try:
            await mark_payment_as_cancelled(session, payment_id)
            logger.info(f"Payment {payment_id} cancelled due to /start")
        except Exception as e:
            logger.warning(f"Failed to cancel payment {payment_id}: {e}")
    await state.clear()
    telegram_id = message.from_user.id
    ref_id = parse_referral_id(command.args) if command.args else None
    user = await get_user_by_telegram_id(session, telegram_id)
    if not user:
        await SubscriptionService.process_onboarding(
            session, telegram_id, message.from_user.username,
            message.from_user.first_name, ref_id,
        )
        user = await get_user_by_telegram_id(session, telegram_id)
    is_active = await SubscriptionService.check_access(session, user.telegram_id)
    is_admin = user.telegram_id in get_settings().ADMIN_IDS
    name = safe(user.first_name or "Пользователь")
    text = texts.HUB_HEADER.format(name=name)
    kb = get_hub_keyboard(is_admin=is_admin, is_active=is_active)
    await render_hub(message.bot, message.chat.id, text, kb)
@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await callback.answer()
    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    payment_id = data.get("payment_id")
    # 🔥 ИСПРАВЛЕНО SMH: используем clear_and_delete_hub вместо clear_hub_cache
    if invoice_message_id or payment_id:
        await clear_and_delete_hub(callback.bot, callback.message.chat.id)
    if payment_id:
        try:
            await mark_payment_as_cancelled(session, payment_id)
        except Exception:
            pass
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    is_active = await SubscriptionService.check_access(session, db_user.telegram_id)
    is_admin = db_user.telegram_id in get_settings().ADMIN_IDS
    name = safe(db_user.first_name or "Пользователь")
    text = texts.HUB_HEADER.format(name=name)
    kb = get_hub_keyboard(is_admin=is_admin, is_active=is_active)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)