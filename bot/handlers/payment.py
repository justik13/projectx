from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.payments_repo import create_payment
from services.subscription import SubscriptionService
from bot.texts import (
    PAYMENT_TARIFFS_HEADER, PAYMENT_METHOD_TEXT, 
    PAYMENT_STARS_CONFIRM, PAYMENT_SUCCESS
)
from bot.keyboards import (
    get_payment_tariff_keyboard, get_payment_method_keyboard, 
    get_payment_confirm_keyboard, get_back_button
)
from bot.states import PaymentStates
from utils.formatters import format_datetime
import logging
from datetime import datetime

router = Router()


@router.message(F.text == "💳 Оплата")
async def show_payment(message: Message):
    """Показать список тарифов"""
    session = await get_session()
    try:
        tariffs = await get_active_tariffs(session)
        
        if not tariffs:
            await message.answer(
                "💳 В данный момент нет доступных тарифов.\n\n"
                "Обратитесь в поддержку для продления доступа."
            )
            return
        
        await message.answer(
            PAYMENT_TARIFFS_HEADER,
            reply_markup=get_payment_tariff_keyboard(tariffs)
        )
    finally:
        await session.close()


@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(callback: CallbackQuery, state: FSMContext):
    """Выбор тарифа — показываем способы оплаты"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff or not tariff.is_active:
            await callback.answer("❌ Тариф недоступен", show_alert=True)
            return
        
        text = PAYMENT_METHOD_TEXT.format(
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=get_payment_method_keyboard(tariff.id)
        )
        await state.update_data(tariff_id=tariff.id)
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(callback: CallbackQuery, state: FSMContext):
    """Оплата через Telegram Stars"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        text = PAYMENT_STARS_CONFIRM.format(price_stars=tariff.price_stars)
        
        await callback.message.edit_text(
            text,
            reply_markup=get_payment_confirm_keyboard(
                payment_id=0,
                amount=tariff.price_stars,
                currency="stars"
            )
        )
        await state.update_data(
            tariff_id=tariff.id,
            payment_method="stars",
            amount=tariff.price_stars
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(callback: CallbackQuery, state: FSMContext):
    """Оплата через СБП"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        text = (
            f"💳 Оплата через СБП\n"
            f"─────────────────────────────\n\n"
            f"К оплате: {tariff.price_rub} ₽\n\n"
            f"📱 Переведите {tariff.price_rub} ₽ на карту:\n"
            f"<code>2200 7007 1234 5678</code>\n"
            f"Получатель: Иван И.\n\n"
            f"После оплаты нажмите кнопку ниже — администратор проверит платёж и активирует доступ."
        )
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Я оплатил", callback_data=f"confirm_payment:{tariff.id}")
        builder.button(text="← Назад", callback_data=f"select_tariff:{tariff.id}")
        builder.adjust(1)
        
        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        await state.update_data(
            tariff_id=tariff.id,
            payment_method="sbp",
            amount=tariff.price_rub
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "back_to_payment")
async def back_to_payment(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору способа оплаты"""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    
    if not tariff_id:
        await callback.answer()
        return
    
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        text = PAYMENT_METHOD_TEXT.format(
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars
        )
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_payment_method_keyboard(tariff.id)
            )
        except Exception as e:
            if "message is not modified" not in str(e):
                raise
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("confirm_payment:"))
async def confirm_payment(callback: CallbackQuery, state: FSMContext):
    """Подтверждение оплаты — выдаём доступ"""
    tariff_id = int(callback.data.split(":")[1])
    telegram_id = callback.from_user.id
    data = await state.get_data()
    
    session = await get_session()
    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        tariff = await get_tariff_by_id(session, tariff_id)
        
        if not user or not tariff:
            await callback.answer("❌ Ошибка данных", show_alert=True)
            return
        
        payment_method = data.get("payment_method", "stars")
        amount = data.get("amount", tariff.price_stars)
        currency = "stars" if payment_method == "stars" else "rub"
        
        # Создаём запись о платеже
        payment = await create_payment(
            session,
            user_id=user.id,
            tariff_id=tariff.id,
            amount=amount,
            currency=currency
        )
        # Устанавливаем статус "оплачен"
        payment.status = "completed"
        payment.paid_at = datetime.utcnow()
        await session.commit()
        await session.refresh(payment)
        
        # Продлеваем подписку
        await SubscriptionService.extend_subscription(
            session,
            telegram_id,
            tariff.duration_days
        )
        
        # Получаем обновлённого пользователя
        user = await get_user_by_telegram_id(session, telegram_id)
        valid_until = format_datetime(user.subscription_end)
        
        text = PAYMENT_SUCCESS.format(
            duration_days=tariff.duration_days,
            valid_until=valid_until
        )
        
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button("back_to_main")
        )
        
        await state.clear()
        await callback.answer("✅ Оплата прошла успешно!", show_alert=True)
        
        logging.info(
            f"Payment completed: user={telegram_id}, "
            f"tariff={tariff.id}, days={tariff.duration_days}, "
            f"method={payment_method}, amount={amount}"
        )
    except Exception as e:
        logging.error(f"Payment error: {e}", exc_info=True)
        await callback.answer(
            "❌ Ошибка при обработке платежа. Обратитесь в поддержку.",
            show_alert=True
        )
    finally:
        await session.close()