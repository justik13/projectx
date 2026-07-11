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
    get_payment_tariff_keyboard,
)
from bot import texts
from database.models import User
from database.repositories.payments_repo import create_payment, get_payment_by_id
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.profiles_repo import get_user_profiles
from services.payment_service import PaymentService
from utils.formatters import format_datetime
from utils.telegram import safe_delete_message

router = Router()
logger = logging.getLogger(__name__)


async def _is_subscription_active(user: User) -> bool:
    """Проверяет, активна ли подписка пользователя."""
    if not user or not user.subscription_end:
        return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return user.subscription_end > now


async def _get_active_devices_count(session: AsyncSession, user: User) -> int:
    """Считает количество активных устройств пользователя."""
    profiles = await get_user_profiles(session, user.id)
    return sum(1 for p in profiles if p.is_active)


@router.message(F.text == "💳 Оплата")
async def show_payment(
    message: Message, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await state.clear()
    await safe_delete_message(message)

    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await message.answer(texts.PAYMENT_NO_TARIFFS)
        return

    # Определяем текущий тариф пользователя для пометки
    current_tariff_id = db_user.current_tariff_id if db_user else None

    await message.answer(
        texts.PAYMENT_TARIFFS_HEADER,
        reply_markup=get_payment_tariff_keyboard(tariffs, current_tariff_id),
        parse_mode="HTML",
    )


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
            # Даунгрейд запрещён во время активной подписки
            await callback.message.edit_text(
                texts.DOWNGRADE_BLOCKED.format(
                    current_limit=current_limit,
                    new_limit=device_limit,
                ),
                reply_markup=get_back_button("back_to_payment_list"),
                parse_mode="HTML",
            )
            await callback.answer()
            return

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
                duration_days=payment.tariff.duration_days,
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

    await callback.message.edit_text(
        texts.PAYMENT_SBP_TEXT.format(price_rub=tariff.price_rub),
        reply_markup=_sbp_confirm_keyboard(tariff.id),
        parse_mode="HTML",
    )
    await state.update_data(tariff_id=tariff.id, payment_method="sbp", amount=tariff.price_rub)
    await callback.answer()


def _sbp_confirm_keyboard(tariff_id: int):
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Оплатить", callback_data=f"confirm_payment_sbp:{tariff_id}")
    builder.button(text="← К выбору тарифа", callback_data=f"select_tariff:{tariff_id}")
    builder.adjust(1)
    return builder.as_markup()


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
                duration_days=tariff.duration_days,
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


@router.callback_query(F.data == "back_to_payment_list")
async def back_to_payment_list(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    """Возврат к списку тарифов после блока даунгрейда."""
    await state.clear()
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await callback.message.edit_text(
            texts.PAYMENT_NO_TARIFFS,
            reply_markup=get_back_button("back_to_main_menu"),
        )
        await callback.answer()
        return

    current_tariff_id = db_user.current_tariff_id if db_user else None

    await callback.message.edit_text(
        texts.PAYMENT_TARIFFS_HEADER,
        reply_markup=get_payment_tariff_keyboard(tariffs, current_tariff_id),
        parse_mode="HTML",
    )
    await callback.answer()