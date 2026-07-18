import logging
from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery, LabeledPrice, Message, PreCheckoutQuery, InlineKeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import (
    get_back_button, get_payment_method_keyboard, get_tariff_showcase_keyboard,
    get_tariff_duration_keyboard, get_renew_keyboard, get_change_tariff_keyboard,
    get_payment_success_keyboard, get_sbp_payment_keyboard,
)
from bot import texts
from database.repositories.payments_repo import (
    create_payment, get_payment_by_id, get_payment_by_id_simple, mark_payment_as_cancelled
)
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.profiles_repo import get_user_profiles, get_user_profiles_count
from services.payment_service import PaymentService
from utils.formatters import format_datetime, format_days_left
from utils.datetime_helpers import is_expired
from utils.telegram import render_hub, send_hub_invoice, clear_and_delete_hub
from utils.tariff_names import get_tariff_display_name

router = Router()
logger = logging.getLogger(__name__)

async def _is_subscription_active(user) -> bool:
    # 🔥 ИЗМЕНЕНО: is_expired() вместо ручного сравнения с naive datetime
    if not user or not user.subscription_end:
        return False
    return not is_expired(user.subscription_end)

@router.callback_query(F.data.in_(["menu_buy", "menu_subscription"]))
async def hub_menu_payment(
    callback: CallbackQuery, state: FSMContext,
    db_user=None, session: AsyncSession = None,
) -> None:
    await callback.answer()
    await state.clear()
    if not db_user:
        return
    is_active = await _is_subscription_active(db_user)
    if is_active:
        await _show_hub(callback, db_user, session)
    else:
        await _show_showcase(callback, session)

async def _show_showcase(callback: CallbackQuery, session: AsyncSession) -> None:
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS, get_back_button("back_to_main_menu")
        )
        return
    grouped: dict[int, list] = {}
    for t in tariffs:
        limit = getattr(t, 'device_limit', 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(t)
    kb = get_tariff_showcase_keyboard(grouped)
    await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_SHOWCASE_HEADER, kb)

async def _show_hub(callback: CallbackQuery, user, session: AsyncSession) -> None:
    profiles = await get_user_profiles(session, user.id)
    tariff_name = get_tariff_display_name(user.device_limit)
    text = texts.PAYMENT_HUB_HEADER.format(
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        tariff_name=tariff_name,
        devices_count=len(profiles),
        device_limit=user.device_limit,
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Продлить доступ", callback_data="payment_quick_renew")
    builder.button(text="⚙️ Сменить тариф", callback_data="payment_change_tariff")
    builder.button(text="👤 Профиль", callback_data="menu_profile")
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1, 1, 1)
    await render_hub(callback.bot, callback.message.chat.id, text, builder.as_markup())

@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    await _show_showcase(callback, session)

@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer()
    device_limit = int(callback.data.split(":")[1])
    tariffs = await get_active_tariffs(session)
    type_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == device_limit]
    if not type_tariffs:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS, get_back_button("payment_showcase")
        )
        return
    desc = texts.PAYMENT_TARIFF_DESCRIPTION.get(device_limit, "")
    text = desc + texts.PAYMENT_DURATION_HEADER
    kb = get_tariff_duration_keyboard(type_tariffs)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)

@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(callback: CallbackQuery, db_user, session: AsyncSession) -> None:
    await callback.answer()
    tariffs = await get_active_tariffs(session)
    current_limit = db_user.device_limit
    renew_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == current_limit]
    if not renew_tariffs:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS, get_back_button("menu_subscription")
        )
        return
    tariff_name = get_tariff_display_name(current_limit)
    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )
    kb = get_renew_keyboard(renew_tariffs)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)

@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(callback: CallbackQuery, db_user, session: AsyncSession) -> None:
    await callback.answer()
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS, get_back_button("menu_subscription")
        )
        return
    current_limit = db_user.device_limit
    tariff_name = get_tariff_display_name(current_limit)
    is_active = await _is_subscription_active(db_user)
    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )
    kb = get_change_tariff_keyboard(tariffs, current_limit, is_subscription_active=is_active)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)

@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(
    callback: CallbackQuery, state: FSMContext,
    db_user=None, session: AsyncSession = None,
) -> None:
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer(texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True)
        return
    device_limit = getattr(tariff, 'device_limit', 2)
    if db_user:
        profiles_count = await get_user_profiles_count(session, db_user.id)
        if profiles_count > device_limit:
            text = texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                profiles_count=profiles_count,
                new_limit=device_limit,
            )
            try:
                await callback.message.edit_text(
                    text,
                    reply_markup=get_back_button("payment_change_tariff"),
                    parse_mode="HTML",
                )
            except TelegramBadRequest as e:
                logger.warning(f"select_tariff edit_text failed: {e}")
            await callback.answer()
            return
    tariff_name = get_tariff_display_name(device_limit)
    text = texts.PAYMENT_CHECKOUT_TEXT.format(
        tariff_name=tariff_name,
        duration_days=tariff.duration_days,
        price_rub=tariff.price_rub,
        price_stars=tariff.price_stars,
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_payment_method_keyboard(tariff.id, device_limit),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.warning(f"select_tariff edit_text failed: {e}")
    await callback.answer()

@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(
    callback: CallbackQuery, state: FSMContext,
    db_user=None, session: AsyncSession = None,
) -> None:
    try:
        await callback.answer("💳 Отправляю инвойс...")
        tariff_id = int(callback.data.split(":")[1])
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff or not db_user:
            return
        payment = await create_payment(
            session=session, user_id=db_user.id,
            tariff_id=tariff.id, amount=tariff.price_stars, currency="stars",
        )
        try:
            invoice_builder = InlineKeyboardBuilder()
            invoice_builder.row(
                InlineKeyboardButton(text="💳 Оплатить", pay=True),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"cancel_invoice:{payment.id}:{tariff.id}"
                )
            )
            invoice_msg_id = await send_hub_invoice(
                callback.bot, callback.message.chat.id,
                title=f"Доступ на {tariff.duration_days} дней ({getattr(tariff, 'device_limit', 2)} устр.)",
                description="Оплата цифрового доступа к защищенным конфигурациям сети.",
                prices=[LabeledPrice(label="Доступ к сети", amount=tariff.price_stars)],
                provider_token="",
                payload=f"stars_payment:{payment.id}",
                currency="XTR",
                start_parameter="network-access-stars",
                reply_markup=invoice_builder.as_markup()
            )
            await state.update_data(
                tariff_id=tariff.id,
                payment_id=payment.id,
                invoice_message_id=invoice_msg_id,
            )
        except TelegramAPIError as e:
            logger.error(f"Failed to send invoice: {e}")
            await render_hub(
                callback.bot, callback.message.chat.id,
                texts.ERROR_PAYMENT_SERVICE,
                get_back_button(f"select_tariff:{tariff_id}")
            )
            payment.status = "failed"
    except Exception as e:
        logger.error(f"pay_stars error: {e}", exc_info=True)
        await callback.answer("❌ Ошибка при создании платежа", show_alert=True)

@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    payload = pre_checkout_query.invoice_payload
    if not payload or not payload.startswith("stars_payment:"):
        await pre_checkout_query.answer(ok=False, error_message="Invalid payment payload")
        return
    try:
        payment_id = int(payload.split(":")[1])
    except (ValueError, IndexError):
        await pre_checkout_query.answer(ok=False, error_message="Invalid payment ID")
        return
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(
    message: Message, state: FSMContext, session: AsyncSession = None
) -> None:
    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    await state.clear()
    await clear_and_delete_hub(message.bot, message.chat.id)
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_payment:"):
        return
    try:
        payment_id = int(payload.split(":")[1])
    except (ValueError, IndexError):
        return
    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        await render_hub(
            message.bot, message.chat.id,
            texts.PAYMENT_DELAYED, get_back_button("menu_subscription")
        )
        return
    if not payment.user or payment.user.telegram_id != message.from_user.id:
        await render_hub(
            message.bot, message.chat.id,
            texts.PAYMENT_DELAYED, get_back_button("menu_subscription")
        )
        return
    if await PaymentService.handle_successful_payment(session, payment_id):
        user = await get_user_by_telegram_id(session, message.from_user.id)
        profiles = await get_user_profiles(session, user.id)
        valid_until = format_datetime(user.subscription_end) if user and user.subscription_end else "—"
        device_limit = getattr(payment.tariff, 'device_limit', 2) if payment.tariff else 2
        tariff_name = get_tariff_display_name(device_limit)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(
                tariff_name=tariff_name, valid_until=valid_until
            )
            if profiles else
            texts.PAYMENT_SUCCESS_NEW.format(
                tariff_name=tariff_name, valid_until=valid_until
            )
        )
        await render_hub(
            message.bot, message.chat.id, text, get_payment_success_keyboard()
        )
    else:
        await render_hub(
            message.bot, message.chat.id,
            texts.PAYMENT_DELAYED, get_back_button("menu_subscription")
        )

@router.callback_query(F.data.startswith("cancel_invoice:"))
async def cancel_invoice(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession = None, db_user=None,
) -> None:
    await callback.answer("❌ Инвойс отменен")
    parts = callback.data.split(":")
    payment_id = int(parts[1])
    tariff_id = int(parts[2])
    if db_user and session:
        payment = await get_payment_by_id_simple(session, payment_id)
        if not payment:
            await callback.answer("Платёж не найден", show_alert=True)
            return
        if payment.user_id != db_user.id:
            await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
            return
        await clear_and_delete_hub(callback.bot, callback.message.chat.id)
        if payment_id:
            try:
                await mark_payment_as_cancelled(session, payment_id)
            except Exception as e:
                logger.warning(f"Failed to cancel payment {payment_id}: {e}")
        await state.clear()
        tariff = await get_tariff_by_id(session, tariff_id)
        if tariff:
            device_limit = getattr(tariff, 'device_limit', 2)
            tariff_name = get_tariff_display_name(device_limit)
            text = texts.PAYMENT_CHECKOUT_TEXT.format(
                tariff_name=tariff_name,
                duration_days=tariff.duration_days,
                price_rub=tariff.price_rub,
                price_stars=tariff.price_stars,
            )
            await render_hub(
                callback.bot, callback.message.chat.id,
                text, get_payment_method_keyboard(tariff.id, device_limit)
            )
            return
    user = await get_user_by_telegram_id(session, callback.from_user.id)
    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)

@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession = None
) -> None:
    try:
        await callback.answer("⏳ Создаю платеж...")
        tariff_id = int(callback.data.split(":")[1])
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer(texts.ERROR_TARIFF_NOT_FOUND, show_alert=True)
            return
        db_user = await get_user_by_telegram_id(session, callback.from_user.id)
        if not db_user:
            await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
            return
        bot_info = await callback.bot.get_me()
        payment, _ = await PaymentService.create_platega_payment(
            session=session,
            user_id=db_user.id,
            tariff_id=tariff.id,
            amount=float(tariff.price_rub),
            telegram_id=db_user.telegram_id,
            bot_username=bot_info.username
        )
        if not payment or not payment.payment_url:
            await render_hub(
                callback.bot, callback.message.chat.id,
                texts.ERROR_PAYMENT_SERVICE,
                get_back_button(f"select_tariff:{tariff_id}")
            )
            return
        await state.update_data(payment_id=payment.id)
        text = texts.PAYMENT_SBP_INSTRUCTIONS.format(
            amount=tariff.price_rub,
            payment_url=payment.payment_url
        )
        await render_hub(
            callback.bot, callback.message.chat.id,
            text,
            get_sbp_payment_keyboard(payment.payment_url, payment.id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"pay_sbp error: {e}", exc_info=True)
        await callback.answer("❌ Ошибка при создании платежа", show_alert=True)

@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession = None
) -> None:
    await callback.answer("⏳ Проверяю статус...")
    payment_id = int(callback.data.split(":")[1])
    success = await PaymentService.check_platega_payment(session, payment_id)
    if success:
        payment = await get_payment_by_id(session, payment_id)
        user = await get_user_by_telegram_id(session, callback.from_user.id)
        profiles = await get_user_profiles(session, user.id)
        valid_until = format_datetime(user.subscription_end) if user and user.subscription_end else "—"
        device_limit = getattr(payment.tariff, 'device_limit', 2) if payment.tariff else 2
        tariff_name = get_tariff_display_name(device_limit)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(
                tariff_name=tariff_name, valid_until=valid_until
            )
            if profiles else
            texts.PAYMENT_SUCCESS_NEW.format(
                tariff_name=tariff_name, valid_until=valid_until
            )
        )
        await render_hub(
            callback.bot, callback.message.chat.id,
            text, get_payment_success_keyboard()
        )
    else:
        await callback.answer("❌ Платёж ещё не поступил. Попробуйте позже.", show_alert=True)