import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import (
    get_back_button,
    get_payment_method_keyboard,
    get_tariff_showcase_keyboard,
    get_tariff_duration_keyboard,
    get_renew_keyboard,
    get_change_tariff_keyboard,
)
from bot import texts
from database.models import User
from database.repositories.payments_repo import create_payment, get_payment_by_id
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.payment_service import PaymentService
from utils.formatters import format_datetime, format_days_left
from utils.telegram import safe_delete_message

router = Router()
logger = logging.getLogger(__name__)

def _get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2:
        return "📱 Базовый"
    elif device_limit <= 5:
        return "👨‍👩‍👧‍👦 Семейный"
    elif device_limit <= 10:
        return "🚀 Pro"
    else:
        return f"🏢 Бизнес ({device_limit} устр.)"

async def _is_subscription_active(user: User) -> bool:
    if not user or not user.subscription_end:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return user.subscription_end > now

@router.message(F.text == "💳 Оплата")
async def show_payment(
    message: Message, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await state.clear()
    await safe_delete_message(message)
    
    if not db_user:
        await message.answer(texts.ERROR_USER_NOT_FOUND)
        return

    is_active = await _is_subscription_active(db_user)
    
    if is_active:
        # Быстрое продление
        await _show_renew_options(message, db_user, session)
    else:
        # Витрина тарифов
        await _show_tariff_showcase(message, session)

async def _show_tariff_showcase(target, session: AsyncSession):
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await target.answer(texts.PAYMENT_NO_TARIFFS)
        return
    
    # Группируем по device_limit
    grouped = {}
    for t in tariffs:
        limit = getattr(t, 'device_limit', 2)
        if limit not in grouped:
            grouped[limit] = []
        grouped[limit].append(t)
    
    kb = get_tariff_showcase_keyboard(grouped)
    await target.answer(
        texts.PAYMENT_SHOWCASE_HEADER,
        reply_markup=kb,
        parse_mode="HTML",
    )

async def _show_renew_options(target, user: User, session: AsyncSession):
    tariffs = await get_active_tariffs(session)
    current_limit = user.device_limit
    
    # Находим тарифы с таким же лимитом
    renew_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == current_limit]
    
    tariff_name = _get_tariff_display_name(current_limit)
    valid_until = format_datetime(user.subscription_end)
    days_left = format_days_left(user.subscription_end)
    
    text = texts.PAYMENT_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=valid_until,
        days_left=days_left,
    )
    
    kb = get_renew_keyboard(renew_tariffs)
    await target.answer(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    await _show_tariff_showcase(callback.message, session)

@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(callback: CallbackQuery, session: AsyncSession):
    device_limit = int(callback.data.split(":")[1])
    await callback.answer()
    
    tariffs = await get_active_tariffs(session)
    type_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == device_limit]
    
    if not type_tariffs:
        await callback.message.edit_text(texts.PAYMENT_NO_TARIFFS, reply_markup=get_back_button("payment_showcase"))
        return
    
    tariff_name = _get_tariff_display_name(device_limit)
    text = texts.PAYMENT_DURATION_HEADER.format(tariff_name=tariff_name)
    
    # Добавляем описание тарифа
    desc = texts.PAYMENT_TARIFF_DESCRIPTION.get(device_limit, "")
    if desc:
        text = desc + "\n\n" + text
    
    kb = get_tariff_duration_keyboard(type_tariffs)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data == "payment_renew")
async def show_renew_callback(callback: CallbackQuery, state: FSMContext, db_user: User, session: AsyncSession):
    await callback.answer()
    await _show_renew_options(callback.message, db_user, session)

@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(callback: CallbackQuery, db_user: User, session: AsyncSession):
    await callback.answer()
    
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await callback.message.edit_text(texts.PAYMENT_NO_TARIFFS)
        return
    
    current_limit = db_user.device_limit
    tariff_name = _get_tariff_display_name(current_limit)
    valid_until = format_datetime(db_user.subscription_end)
    
    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=valid_until,
    )
    
    kb = get_change_tariff_keyboard(tariffs, current_limit)
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer(texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True)
        return
    
    device_limit = getattr(tariff, 'device_limit', 2)
    
    # === ПРОВЕРКА ДАУНГРЕЙДА ===
    if db_user and await _is_subscription_active(db_user):
        current_limit = db_user.device_limit
        if device_limit < current_limit:
            valid_until = format_datetime(db_user.subscription_end)
            await callback.message.edit_text(
                texts.DOWNGRADE_BLOCKED.format(
                    current_limit=current_limit,
                    new_limit=device_limit,
                    valid_until=valid_until,
                ),
                reply_markup=get_back_button("payment_change_tariff"),
                parse_mode="HTML",
            )
            await callback.answer()
            return
    
    # Показываем выбор метода оплаты
    await callback.message.edit_text(
        texts.PAYMENT_METHOD_TEXT.format(
            duration_days=tariff.duration_days,
            device_limit=device_limit,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars,
        ),
        reply_markup=get_payment_method_keyboard(tariff.id),
        parse_mode="HTML",
    )
    await state.update_data(tariff_id=tariff.id)
    await callback.answer()

# ============================================================
# Telegram Stars
# ============================================================
@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not db_user:
        await callback.answer(texts.ERROR_PAYMENT_DATA_INVALID, show_alert=True)
        return
    
    if tariff.price_stars <= 0:
        logger.error(f"Tariff {tariff.id} has invalid price_stars={tariff.price_stars}")
        await callback.answer(texts.ERROR_TARIFF_INVALID_PRICE, show_alert=True)
        return
    
    payment = await create_payment(
        session=session,
        user_id=db_user.id,
        tariff_id=tariff.id,
        amount=tariff.price_stars,
        currency="stars",
    )
    
    await safe_delete_message(callback.message)
    
    try:
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Подписка на {tariff.duration_days} дней ({getattr(tariff, 'device_limit', 2)} устр.)",
            description="Оплата цифрового доступа к защищенным конфигурациям сети.",
            prices=[LabeledPrice(label="Доступ к сети", amount=tariff.price_stars)],
            provider_token="",
            payload=f"stars_payment:{payment.id}",
            currency="XTR",
            start_parameter="network-access-stars",
        )
    except TelegramAPIError as e:
        logger.error(f"Failed to send invoice: {e}")
        await callback.message.answer(
            texts.ERROR_PAYMENT_SERVICE,
            reply_markup=get_back_button("back_to_main_menu"),
        )
        payment.status = "failed"
        await session.commit()
        return
    
    await state.clear()
    await callback.answer()

@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext, session: AsyncSession = None):
    await state.clear()
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_payment:"):
        return
    
    payment_id = int(payload.split(":")[1])
    
    if await PaymentService.handle_successful_payment(session, payment_id):
        payment = await get_payment_by_id(session, payment_id)
        
        # СВЕЖИЙ запрос пользователя после оплаты
        user = await get_user_by_telegram_id(session, message.from_user.id)
        valid_until = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )
        device_limit = getattr(payment.tariff, 'device_limit', 2) if payment.tariff else 2
        
        await message.answer(
            texts.PAYMENT_SUCCESS.format(
                valid_until=valid_until,
                device_limit=device_limit,
            ),
            reply_markup=get_back_button("back_to_main_menu"),
            parse_mode="HTML",
        )
    else:
        await message.answer(texts.PAYMENT_DELAYED)

# ============================================================
# СБП
# ============================================================
@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Оплатить", callback_data=f"confirm_payment_sbp:{tariff_id}")
    builder.button(text="← К выбору тарифа", callback_data=f"select_tariff:{tariff_id}")
    builder.adjust(1)
    sbp_kb = builder.as_markup()
    
    await callback.message.edit_text(
        texts.PAYMENT_SBP_TEXT.format(price_rub=tariff.price_rub),
        reply_markup=sbp_kb,
        parse_mode="HTML",
    )
    await state.update_data(tariff_id=tariff.id, payment_method="sbp", amount=tariff.price_rub)
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_payment_sbp:"))
async def confirm_payment_sbp(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    
    payment = await create_payment(
        session=session,
        user_id=db_user.id,
        tariff_id=tariff.id,
        amount=tariff.price_rub,
        currency="rub",
    )
    
    if await PaymentService.handle_successful_payment(session, payment.id):
        # === ФИКС: СВЕЖИЙ запрос пользователя после оплаты ===
        fresh_user = await get_user_by_telegram_id(session, callback.from_user.id)
        valid_until = (
            format_datetime(fresh_user.subscription_end)
            if fresh_user and fresh_user.subscription_end
            else "—"
        )
        device_limit = getattr(tariff, 'device_limit', 2)
        
        await callback.message.edit_text(
            texts.PAYMENT_SUCCESS.format(
                valid_until=valid_until,
                device_limit=device_limit,
            ),
            reply_markup=get_back_button("back_to_main_menu"),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            texts.PAYMENT_DELAYED,
            reply_markup=get_back_button("back_to_main_menu"),
            parse_mode="HTML",
        )

@router.callback_query(F.data == "back_to_payment")
async def back_to_payment(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    await state.clear()
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    if not tariff_id:
        await callback.answer()
        return
    
    tariff = await get_tariff_by_id(session, tariff_id)
    device_limit = getattr(tariff, 'device_limit', 2)
    
    try:
        await callback.message.edit_text(
            texts.PAYMENT_METHOD_TEXT.format(
                duration_days=tariff.duration_days,
                device_limit=device_limit,
                price_rub=tariff.price_rub,
                price_stars=tariff.price_stars,
            ),
            reply_markup=get_payment_method_keyboard(tariff.id),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        pass
    await callback.answer()