# bot/handlers/profile.py
import html
import logging
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id, get_user_referrals
from database.repositories.profiles_repo import get_user_profiles, get_user_profiles_count
from services.subscription import SubscriptionService
from bot.texts import PROFILE_TEXT, REFERRAL_TEXT
from bot.keyboards import get_profile_keyboard, get_referral_keyboard, get_back_button
from utils.formatters import format_traffic, format_datetime, format_days_left
from config.settings import get_settings
from database.models import User

router = Router()
logger = logging.getLogger(__name__)


@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message, db_user: User | None = None):
    """Показать профиль пользователя"""
    user = db_user
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return

    session = await get_session()
    try:
        profiles = await get_user_profiles(session, user.id)
        profiles_count = len(profiles)
        total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)

        has_access = await SubscriptionService.check_access(session, user.telegram_id)
        status_emoji = "🟢" if has_access else "🔴"
        status_text = "Активен" if has_access else "Неактивен"

        valid_until = format_datetime(user.subscription_end)
        days_left = format_days_left(user.subscription_end)
        total_traffic_str = format_traffic(total_traffic)

        # Защита от HTML-инъекций и фикс реферального счетчика
        safe_name = html.escape(user.first_name or "Пользователь")
        safe_username = html.escape(user.username or "—")
        referrals_count = len(await get_user_referrals(session, user.telegram_id))

        text = PROFILE_TEXT.format(
            name=safe_name,
            username=safe_username,
            telegram_id=user.telegram_id,
            status_emoji=status_emoji,
            status_text=status_text,
            valid_until=valid_until,
            days_left=days_left,
            devices_count=profiles_count,
            device_limit=user.device_limit,
            total_traffic=total_traffic_str,
            referrals_count=referrals_count,
            referral_days=user.referral_days
        )

        await message.answer(
            text,
            reply_markup=get_profile_keyboard(),
            parse_mode="HTML"  # Исправлено: PROFILE_TEXT содержит HTML-теги оформления (<b>)
        )
    finally:
        await session.close()


@router.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery, db_user: User | None = None):
    """Показать экран рефералов"""
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    session = await get_session()
    try:
        settings = get_settings()
        bot_info = await callback.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{user.telegram_id}"

        # Исправлено: Считаем реальное количество рефералов из базы вместо хрупкого деления дней
        invited_count = len(await get_user_referrals(session, user.telegram_id))

        text = REFERRAL_TEXT.format(
            bonus_days=settings.REFERRAL_BONUS_DAYS,
            referral_link=referral_link,
            invited_count=invited_count,
            bonus_total=user.referral_days
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_referral_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "referrals_list")
async def show_referrals_list(callback: CallbackQuery, db_user: User | None = None):
    """Показать список рефералов"""
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return

    session = await get_session()
    try:
        referrals = await get_user_referrals(session, user.telegram_id)
        
        if not referrals:
            text = "👥 Список рефералов пока пуст.\n\nПригласите друзей по вашей ссылке, чтобы они появились здесь."
        else:
            settings = get_settings()
            text = "👥 Ваши рефералы:\n"
            text += "─────────────────────────────\n\n"
            
            for referral in referrals:
                # Санитируем юзернеймы приглашенных для безопасности разметки
                safe_user_string = f"@{html.escape(referral.username)}" if referral.username else f"ID: {referral.telegram_id}"
                bonus_days = settings.REFERRAL_BONUS_DAYS
                text += f"• {safe_user_string} — {bonus_days} бонусных дней\n"
            
            text += f"\nВсего приглашено: {len(referrals)} пользователей"

        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("referral"),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.in_(["back_to_profile", "back_to_main"]))
async def back_to_profile_or_main(callback: CallbackQuery, db_user: User | None = None):
    """Возврат к профилю или главному меню"""
    if callback.data == "back_to_profile":
        user = db_user
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        session = await get_session()
        try:
            profiles = await get_user_profiles(session, user.id)
            profiles_count = len(profiles)
            total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)
            has_access = await SubscriptionService.check_access(session, user.telegram_id)
            status_emoji = "🟢" if has_access else "🔴"
            status_text = "Активен" if has_access else "Неактивен"

            # Защита от HTML-инъекций и фикс математики рефералов
            safe_name = html.escape(user.first_name or "Пользователь")
            safe_username = html.escape(user.username or "—")
            referrals_count = len(await get_user_referrals(session, user.telegram_id))

            text = PROFILE_TEXT.format(
                name=safe_name,
                username=safe_username,
                telegram_id=user.telegram_id,
                status_emoji=status_emoji,
                status_text=status_text,
                valid_until=format_datetime(user.subscription_end),
                days_left=format_days_left(user.subscription_end),
                devices_count=profiles_count,
                device_limit=user.device_limit,
                total_traffic=format_traffic(total_traffic),
                referrals_count=referrals_count,
                referral_days=user.referral_days
            )

            await callback.message.edit_text(
                text,
                reply_markup=get_profile_keyboard(),
                parse_mode="HTML"  # Исправлено: PROFILE_TEXT содержит HTML-теги оформления
            )
            await callback.answer()
        finally:
            await session.close()
    else:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    """Удаляет инлайн-интерфейс, возвращая фокус на Reply-клавиатуру нижнего меню"""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()