import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.repositories.users_repo import get_user_by_telegram_id, get_user_referrals
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.payments_repo import get_user_payments
from services.subscription import SubscriptionService
from bot.texts import PROFILE_TEXT, REFERRAL_TEXT
from bot.keyboards import (
    get_profile_keyboard, get_referral_keyboard,
    get_back_button, get_history_keyboard
)
from utils.formatters import format_traffic, format_datetime, format_days_left
from config.settings import get_settings
from database.models import User
from sqlalchemy.ext.asyncio import AsyncSession
from utils.telegram import safe

router = Router()
logger = logging.getLogger(__name__)


@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    user = db_user
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)
    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    status_emoji = "🟢" if has_access else "🔴"
    status_text = "Активен" if has_access else "Неактивен"
    valid_until = format_datetime(user.subscription_end)
    days_left = format_days_left(user.subscription_end)
    total_traffic_str = format_traffic(total_traffic)
    safe_name = safe(user.first_name or "Пользователь")
    safe_username = safe(user.username or "—")
    referrals_count = len(await get_user_referrals(session, user.telegram_id))
    text = PROFILE_TEXT.format(
        name=safe_name, username=safe_username, telegram_id=user.telegram_id,
        status_emoji=status_emoji, status_text=status_text, valid_until=valid_until,
        days_left=days_left, devices_count=profiles_count, device_limit=user.device_limit,
        total_traffic=total_traffic_str, referrals_count=referrals_count,
        referral_days=user.referral_days
    )
    await message.answer(text, reply_markup=get_profile_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "user_history")
async def show_history(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    payments = await get_user_payments(session, user.id)
    text = "🧾 <b>История оплат</b>\n\n"
    if not payments:
        text += "_История пуста. У вас пока не было оплат._"
    else:
        for p in payments[:10]:
            status = "✅" if p.status == 'completed' else "⏳"
            date = format_datetime(p.paid_at or p.created_at)
            currency = "⭐" if p.currency == "stars" else "₽"
            text += f"{status} {date} | {p.amount} {currency}\n"
        if len(payments) > 10:
            text += f"\n<i>Показаны последние 10 из {len(payments)} оплат</i>"
    await callback.message.edit_text(
        text,
        reply_markup=get_history_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    settings = get_settings()
    bot_info = await callback.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{user.telegram_id}"
    invited_count = len(await get_user_referrals(session, user.telegram_id))
    text = REFERRAL_TEXT.format(
        bonus_days=settings.REFERRAL_BONUS_DAYS, referral_link=referral_link,
        invited_count=invited_count, bonus_total=user.referral_days
    )
    await callback.message.edit_text(
        text,
        reply_markup=get_referral_keyboard(referral_link),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "referrals_list")
async def show_referrals_list(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    referrals = await get_user_referrals(session, user.telegram_id)
    if not referrals:
        text = (
            "👥 <b>Список рефералов</b>\n\n"
            "Список рефералов пока пуст.\n"
            "Пригласите друзей по вашей ссылке, чтобы они появились здесь."
        )
    else:
        settings = get_settings()
        text = "👥 <b>Ваши рефералы</b>\n\n"
        for referral in referrals:
            safe_user_string = (
                f"@{safe(referral.username)}" if referral.username
                else f"ID: {referral.telegram_id}"
            )
            bonus_days = settings.REFERRAL_BONUS_DAYS
            text += f"• {safe_user_string} — {bonus_days} бонусных дней\n"
        text += f"\nВсего приглашено: {len(referrals)} пользователей"
    await callback.message.edit_text(
        text, reply_markup=get_back_button("referral"), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)
    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    status_emoji = "🟢" if has_access else "🔴"
    status_text = "Активен" if has_access else "Неактивен"
    safe_name = safe(user.first_name or "Пользователь")
    safe_username = safe(user.username or "—")
    referrals_count = len(await get_user_referrals(session, user.telegram_id))
    text = PROFILE_TEXT.format(
        name=safe_name, username=safe_username, telegram_id=user.telegram_id,
        status_emoji=status_emoji, status_text=status_text,
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        devices_count=profiles_count, device_limit=user.device_limit,
        total_traffic=format_traffic(total_traffic),
        referrals_count=referrals_count, referral_days=user.referral_days
    )
    await callback.message.edit_text(
        text, reply_markup=get_profile_keyboard(), parse_mode="HTML"
    )
    await callback.answer()
