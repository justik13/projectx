import logging
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import get_back_button, get_history_keyboard, get_profile_keyboard, get_referral_keyboard
from bot import texts
from config.settings import get_settings
from database.models import User
from database.repositories.payments_repo import get_user_payments
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.users_repo import get_user_referrals
from database.repositories.tariffs_repo import get_tariff_by_id
from services.subscription import SubscriptionService
from utils.formatters import format_datetime, format_days_left, format_traffic
from utils.telegram import safe

router = Router()
logger = logging.getLogger(__name__)

def _get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2: return "📱 Базовый"
    elif device_limit <= 5: return "👨‍👩‍👧‍👦 Семейный"
    elif device_limit <= 10: return "🚀 Pro"
    else: return "🏢 Бизнес"

async def _render_profile(target, user: User, session: AsyncSession, *, edit: bool):
    from bot.keyboards import get_back_button
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    total_traffic = sum(p.traffic_down + p.traffic_up for p in profiles)
    has_access = await SubscriptionService.check_access(session, user.telegram_id)
    referrals_count = len(await get_user_referrals(session, user.telegram_id))

    tariff_name = "—"
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            dl = getattr(tariff, 'device_limit', user.device_limit)
            tariff_name = f"{_get_tariff_display_name(dl)} ({dl} устр.)"
    elif user.device_limit:
        tariff_name = f"{_get_tariff_display_name(user.device_limit)} ({user.device_limit} устр.)"

    rendered = texts.PROFILE_TEXT.format(
        name=safe(user.first_name or "Пользователь"),
        username=safe(user.username or "—"),
        telegram_id=user.telegram_id,
        status_emoji=("🟢" if has_access else "🔴"),
        status_text=("Активен" if has_access else "Неактивен"),
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        devices_count=profiles_count,
        device_limit=user.device_limit,
        total_traffic=format_traffic(total_traffic),
        referrals_count=referrals_count,
        referral_days=user.referral_days,
        tariff_name=tariff_name,
    )

    # 🔧 ФИКС: Для неактивных пользователей добавляем кнопку "Купить доступ"
    if has_access:
        kb = get_profile_keyboard(is_active=True)
    else:
        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Купить доступ", callback_data="menu_buy")
        builder.button(text="🎁 Пригласить друга", callback_data="referral")
        builder.button(text="🧾 История оплат", callback_data="user_history")
        builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        builder.adjust(1, 1, 1, 1)
        kb = builder.as_markup()

    if edit:
        try:
            await target.edit_text(rendered, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass
    else:
        await target.answer(rendered, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "menu_profile")
async def hub_menu_profile(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_profile(callback.message, db_user, session, edit=True)
    await callback.answer()

@router.callback_query(F.data == "back_to_profile")
async def back_to_profile(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_profile(callback.message, db_user, session, edit=True)
    await callback.answer()

@router.callback_query(F.data == "user_history")
async def show_history(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    payments = await get_user_payments(session, db_user.id)
    if not payments:
        rendered = texts.HISTORY_HEADER + texts.HISTORY_EMPTY
    else:
        rendered = texts.HISTORY_HEADER
        for p in payments[:10]:
            status = "✅" if p.status == "completed" else "⏳"
            date = format_datetime(p.paid_at or p.created_at)
            currency = "⭐" if p.currency == "stars" else "₽"
            rendered += f"{status} {date} | {p.amount} {currency}\n"
        if len(payments) > 10:
            rendered += texts.HISTORY_LIMIT_NOTE.format(count=len(payments))
    await callback.message.edit_text(rendered, reply_markup=get_history_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "referral")
async def show_referral(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    bot_info = await callback.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start=ref_{db_user.telegram_id}"
    invited_count = len(await get_user_referrals(session, db_user.telegram_id))
    await callback.message.edit_text(texts.REFERRAL_TEXT.format(bonus_days=get_settings().REFERRAL_BONUS_DAYS, referral_link=referral_link, invited_count=invited_count, bonus_total=db_user.referral_days), reply_markup=get_referral_keyboard(referral_link), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "referrals_list")
async def show_referrals_list(callback: CallbackQuery, state: FSMContext, db_user: User | None = None, session: AsyncSession = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    referrals = await get_user_referrals(session, db_user.telegram_id)
    bonus_days = get_settings().REFERRAL_BONUS_DAYS
    if not referrals:
        rendered = texts.REFERRAL_LIST_EMPTY
    else:
        rendered = texts.REFERRAL_LIST_HEADER
        for referral in referrals:
            safe_user = f"@{safe(referral.username)}" if referral.username else f"ID: {referral.telegram_id}"
            rendered += f"• {safe_user} — {bonus_days} бонусных дней\n"
        rendered += texts.REFERRAL_LIST_FOOTER.format(count=len(referrals))
    await callback.message.edit_text(rendered, reply_markup=get_back_button("referral"), parse_mode="HTML")
    await callback.answer()