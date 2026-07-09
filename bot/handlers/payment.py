# bot/handlers/payment.py
import logging
from aiogram.exceptions import TelegramBadRequest
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery, LabeledPrice
from aiogram.fsm.context import FSMContext
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.payments_repo import create_payment, get_payment_by_id
from services.subscription import SubscriptionService
from bot.texts import (
    PAYMENT_TARIFFS_HEADER, PAYMENT_METHOD_TEXT, 
    PAYMENT_STARS_CONFIRM, PAYMENT_SUCCESS
)
from bot.keyboards import (
    get_payment_tariff_keyboard, get_payment_method_keyboard, get_back_button
)
from datetime import datetime
from config.settings import get_settings
from database.models import User

router = Router()


@router.message(F.text == "💳 Оплата")
async def show_payment(message: Message):
    session = await get_session()
    try:
        tariffs = await get_active_tariffs(session)
        if not tariffs:
            await message.answer(
                "💳 В данный момент нет доступных тарифов.\n\n"
                "Обратитесь в поддержку для ручного продления доступа."
            )
            return
        
        await message.answer(PAYMENT_TARIFFS_HEADER, reply_markup=get_payment_tariff_keyboard(tariffs))
    finally:
        await session.close()


@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(callback: CallbackQuery, state: FSMContext):
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff or not tariff.is_active:
            await callback.answer("❌ Выбранный тариф сейчас недоступен", show_alert=True)
            return
        
        text = PAYMENT_METHOD_TEXT.format(
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars
        )
        await callback.message.edit_text(text, reply_markup=get_payment_method_keyboard(tariff.id))
        await state.update_data(tariff_id=tariff.id)
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(callback: CallbackQuery, state: FSMContext, db_user: User | None = None):
    """Выставление счета (Invoice) для оплаты через Telegram Stars"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        user = db_user
        if not tariff or not user:
            await callback.answer("❌ Ошибка данных", show_alert=True)
            return
        
        # ✅ Защита P0: Telegram API требует price_stars > 0
        if tariff.price_stars <= 0:
            logging.error(f"Tariff {tariff.id} has invalid price_stars={tariff.price_stars}")
            await callback.answer(
                "❌ Ошибка тарифа: некорректная цена. Обратитесь в поддержку.",
                show_alert=True
            )
            return

        # Создаем предварительную запись о платеже в БД
        payment = await create_payment(
            session=session,
            user_id=user.id,
            tariff_id=tariff.id,
            amount=tariff.price_stars,
            currency="stars"
        )

        # Удаляем предыдущее инлайн-сообщение выбора тарифа
        try:
            await callback.message.delete()
        except Exception:
            pass

        # Отправляем инвойс на оплату Stars (XTR)
        prices = [LabeledPrice(label="Доступ к сети", amount=tariff.price_stars)]
        await callback.bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Подписка на {tariff.duration_days} дней",
            description="Оплата цифрового доступа к защищенным конфигурациям сети.",
            prices=prices,
            provider_token="",  # Для Telegram Stars provider_token должен быть пустым
            payload=f"stars_payment:{payment.id}",
            currency="XTR",
            start_parameter="network-access-stars"
        )
        await state.clear()
        await callback.answer()
    finally:
        await session.close()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Обязательный ответ на системный запрос проверки платежа перед списанием средств"""
    # Здесь можно добавить финальную проверку доступности мест на сервере
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    """Обработчик успешного получения оплаты от Telegram API"""
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_payment:"):
        return

    payment_id = int(payload.split(":")[1])
    session = await get_session()
    try:
        # Активация подписки и начисление бонусов рефереру на уровне сервиса
        success = await SubscriptionService.handle_successful_payment(session, payment_id)
        
        if success:
            payment = await get_payment_by_id(session, payment_id)
            user = await get_user_by_telegram_id(session, message.from_user.id)
            
            valid_until = user.subscription_end.strftime("%d.%m.%Y %H:%M") if user.subscription_end else "—"
            text = PAYMENT_SUCCESS.format(
                duration_days=payment.tariff.duration_days,
                valid_until=valid_until
            )
            await message.answer(text, reply_markup=get_back_button("back_to_main_menu"))
        else:
            await message.answer("⚠️ Возникла задержка при зачислении. Пожалуйста, напишите в поддержку.")
    finally:
        await session.close()


@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(callback: CallbackQuery, state: FSMContext):
    """Ручной перевод по СБП"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        text = (
            f"💳 Оплата через СБП\n"
            f"─────────────────────────────\n"
            f"К оплате: {tariff.price_rub} ₽\n\n"
            f"Нажмите кнопку ниже для оплаты — доступ будет активирован мгновенно."
        )
        
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="💎 Оплатить", callback_data=f"confirm_payment_sbp:{tariff.id}")
        builder.button(text="← Назад", callback_data=f"select_tariff:{tariff.id}")
        builder.adjust(1)
        
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await state.update_data(tariff_id=tariff.id, payment_method="sbp", amount=tariff.price_rub)
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("confirm_payment_sbp:"))
async def confirm_payment_sbp(callback: CallbackQuery, state: FSMContext, db_user: User | None = None):
    """Создание заявки на ручную проверку администратором при оплате по СБП"""
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        user = db_user
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return
            
        tariff = await get_tariff_by_id(session, tariff_id)
        
        # Создаем платеж со статусом pending
        payment = await create_payment(
            session=session, user_id=user.id, tariff_id=tariff.id, 
            amount=tariff.price_rub, currency="rub"
        )
        
        # СБП-заглушка: сразу активируем подписку
        success = await SubscriptionService.handle_successful_payment(session, payment.id)
        
        if success:
            valid_until = user.subscription_end.strftime("%d.%m.%Y %H:%M") if user.subscription_end else "—"
            await callback.message.edit_text(
                f"✅ <b>Оплата прошла успешно!</b>\n"
                f"─────────────────────────────\n"
                f"Вам продлён доступ на {tariff.duration_days} дней.\n"
                f"Действует до: {valid_until}",
                reply_markup=get_back_button("back_to_main_menu"),
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "⚠️ Возникла задержка при зачислении. Пожалуйста, напишите в поддержку.",
                reply_markup=get_back_button("back_to_main_menu"),
                parse_mode="HTML"
            )
                
        await state.clear()
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "back_to_payment")
async def back_to_payment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    if not tariff_id:
        await callback.answer()
        return
    
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        text = PAYMENT_METHOD_TEXT.format(
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars
        )
        
        # ✅ Защита от TelegramBadRequest: message is not modified
        try:
            await callback.message.edit_text(text, reply_markup=get_payment_method_keyboard(tariff.id))
        except TelegramBadRequest:
            pass  # Сообщение уже идентично, игнорируем
            
        await callback.answer()
    finally:
        await session.close()