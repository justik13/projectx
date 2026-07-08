from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.profiles_repo import get_user_profiles, get_user_profiles_count
from services.subscription import SubscriptionService
from bot.texts import PROFILE_TEXT, REFERRAL_TEXT, REFERRALS_LIST_HEADER, REFERRAL_ITEM
from bot.keyboards import get_profile_keyboard, get_referral_keyboard, get_back_button
from utils.formatters import format_traffic, format_datetime, format_days_left
from config.settings import get_settings
import logging

router = Router()


@router.message(F.text == "👤 Профиль")
async def show_profile(message: Message):
    """Показать профиль пользователя"""
    telegram_id = message.from_user.id
    session = await get_session()

    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await message.answer("❌ Пользователь не найден.")
            return

        profiles_count = await get_user_profiles_count(session, user.id)
        profiles = await get_user_profiles(session, user.id)
        total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)

        has_access = await SubscriptionService.check_access(session, telegram_id)
        status_emoji = "🟢" if has_access else "🔴"
        status_text = "Активен" if has_access else "Неактивен"

        valid_until = format_datetime(user.subscription_end)
        days_left = format_days_left(user.subscription_end)
        total_traffic_str = format_traffic(total_traffic)

        text = PROFILE_TEXT.format(
            name=user.first_name or "Пользователь",
            username=user.username or "—",
            telegram_id=user.telegram_id,
            status_emoji=status_emoji,
            status_text=status_text,
            valid_until=valid_until,
            days_left=days_left,
            devices_count=profiles_count,
            device_limit=user.device_limit,
            total_traffic=total_traffic_str,
            referrals_count=user.referral_days // get_settings().REFERRAL_BONUS_DAYS if user.referral_days > 0 else 0,
            referral_days=user.referral_days
        )

        await message.answer(
            text,
            reply_markup=get_profile_keyboard(),
            parse_mode=None
        )
    finally:
        await session.close()


@router.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery):
    """Показать экран рефералов"""
    telegram_id = callback.from_user.id
    session = await get_session()

    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        settings = get_settings()
        bot_info = await callback.bot.get_me()
        referral_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"

        invited_count = user.referral_days // settings.REFERRAL_BONUS_DAYS if user.referral_days > 0 else 0

        text = REFERRAL_TEXT.format(
            bonus_days=settings.REFERRAL_BONUS_DAYS,
            referral_link=referral_link,
            invited_count=invited_count,
            bonus_total=user.referral_days
        )

        await callback.message.edit_text(
            text,
            reply_markup=get_referral_keyboard()
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "referrals_list")
async def show_referrals_list(callback: CallbackQuery):
    """Показать список рефералов (заглушка для MVP)"""
    await callback.message.edit_text(
        "👥 Список рефералов пока пуст.\n\nПригласите друзей по вашей ссылке, чтобы они появились здесь.",
        reply_markup=get_back_button("referral")
    )
    await callback.answer()


@router.callback_query(F.data.in_(["back_to_profile", "back_to_main"]))
async def back_to_profile_or_main(callback: CallbackQuery):
    """Возврат к профилю или главному меню"""
    if callback.data == "back_to_profile":
        telegram_id = callback.from_user.id
        session = await get_session()
        try:
            user = await get_user_by_telegram_id(session, telegram_id)
            if not user:
                await callback.answer("❌ Пользователь не найден", show_alert=True)
                return

            profiles_count = await get_user_profiles_count(session, user.id)
            profiles = await get_user_profiles(session, user.id)
            total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)
            has_access = await SubscriptionService.check_access(session, telegram_id)
            status_emoji = "🟢" if has_access else "🔴"
            status_text = "Активен" if has_access else "Неактивен"

            text = PROFILE_TEXT.format(
                name=user.first_name or "Пользователь",
                username=user.username or "—",
                telegram_id=user.telegram_id,
                status_emoji=status_emoji,
                status_text=status_text,
                valid_until=format_datetime(user.subscription_end),
                days_left=format_days_left(user.subscription_end),
                devices_count=profiles_count,
                device_limit=user.device_limit,
                total_traffic=format_traffic(total_traffic),
                referrals_count=user.referral_days // get_settings().REFERRAL_BONUS_DAYS if user.referral_days > 0 else 0,
                referral_days=user.referral_days
            )

            await callback.message.edit_text(
                text,
                reply_markup=get_profile_keyboard()
            )
            await callback.answer()
        finally:
            await session.close()
    else:
        await callback.message.delete()
        await callback.answer()

@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu(callback: CallbackQuery):
    """Удаляет инлайн-интерфейс, возвращая фокус на Reply-клавиатуру нижнего меню"""
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()