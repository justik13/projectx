import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_payment_success_keyboard,
    get_sbp_payment_keyboard,
)
from config.settings import get_settings
from database.repositories.payments_repo import (
    get_payment_by_id,
    get_payment_by_id_simple,
)
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.maintenance_service import MaintenanceService
from services.payment_service import PaymentService
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub, safe

from .common import (
    _check_tariff_change_allowed,
    _render_maintenance,
)

router = Router()
logger = logging.getLogger(__name__)


def _get_payment_tariff_name(payment) -> str:
    """
    Возвращает отображаемое имя тарифа для платежа.
    Приоритет:
    1. snapshot_device_limit из платежа;
    2. текущий тариф, если snapshot отсутствует.
    """
    device_limit = getattr(
        payment,
        "snapshot_device_limit",
        None,
    )
    if device_limit is None and payment.tariff:
        device_limit = getattr(
            payment.tariff,
            "device_limit",
            2,
        )
    if device_limit is None:
        device_limit = 2

    return get_tariff_display_name(device_limit)


@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
) -> None:
    if session is None:
        await callback.answer()
        return

    parts = callback.data.split(":")
    try:
        tariff_id = int(parts[1])
        source = parts[2] if len(parts) > 2 else "showcase"
    except (ValueError, IndexError):
        await callback.answer()
        return

    back_callback = {
        "change": "payment_change_tariff",
        "renew": "payment_quick_renew",
    }.get(source, f"select_tariff:{tariff_id}:{source}")

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await callback.answer()
        await _render_maintenance(
            callback,
            session,
            back_to=back_callback,
        )
        return

    try:
        await callback.answer(texts.PAYMENT_CREATING)

        #
        # Серверная защита:
        # если Platega не настроена, SBP-платёж создавать нельзя.
        #
        settings = get_settings()
        if (
            not settings.PLATEGA_MERCHANT_ID
            or not settings.PLATEGA_SECRET
        ):
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_SBP_UNAVAILABLE,
                get_back_button(back_callback),
            )
            return

        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer(
                texts.ERROR_TARIFF_NOT_FOUND,
                show_alert=True,
            )
            return

        if not tariff.is_active:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_TARIFF_UNAVAILABLE,
                get_back_button(back_callback),
            )
            return

        db_user = await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )
        if not db_user:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
            return

        error_text = await _check_tariff_change_allowed(
            session,
            db_user,
            tariff,
        )
        if error_text:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                error_text,
                get_back_button(back_callback),
            )
            return

        bot_info = await callback.bot.get_me()

        payment, _ = await PaymentService.create_platega_payment(
            session=session,
            user_id=db_user.id,
            tariff_id=tariff.id,
            amount=float(tariff.price_rub),
            telegram_id=db_user.telegram_id,
            bot_username=bot_info.username,
        )

        if not payment or not payment.payment_url:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_PAYMENT_SERVICE,
                get_back_button(back_callback),
            )
            return

        await state.update_data(payment_id=payment.id)

        text = texts.PAYMENT_SBP_INSTRUCTIONS.format(
            amount=tariff.price_rub,
            payment_url=safe(payment.payment_url),
        )

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_sbp_payment_keyboard(
                payment.payment_url,
                payment.id,
                tariff.id,
                source,
            ),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"pay_sbp error: {e}", exc_info=True)
        await callback.answer(
            texts.PAYMENT_CREATE_ERROR,
            show_alert=True,
        )


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
    db_user=None,
) -> None:
    await callback.answer(texts.PAYMENT_CHECKING_STATUS)

    try:
        payment_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer(
            texts.PAYMENT_INVALID,
            show_alert=True,
        )
        return

    if not db_user or not session:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment_simple = await get_payment_by_id_simple(
        session,
        payment_id,
    )
    if not payment_simple:
        await callback.answer(
            texts.PAYMENT_NOT_FOUND_SHORT,
            show_alert=True,
        )
        return

    if payment_simple.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    success, result_code = await PaymentService.check_platega_payment(
        session,
        payment_id,
    )

    if success and result_code in ("success", "already_processed"):
        payment = await get_payment_by_id(session, payment_id)
        user = await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )
        profiles = (
            await get_user_profiles(session, user.id)
            if user
            else []
        )
        valid_until = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )
        tariff_name = _get_payment_tariff_name(payment)

        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(
                tariff_name=tariff_name,
                valid_until=valid_until,
            )
            if profiles
            else texts.PAYMENT_SUCCESS_NEW.format(
                tariff_name=tariff_name,
                valid_until=valid_until,
            )
        )

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_payment_success_keyboard(),
        )

    elif result_code == "paid_after_cancel":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")
        payment = await get_payment_by_id(session, payment_id)
        tariff_name = _get_payment_tariff_name(payment)

        text = texts.PAYMENT_PAID_AFTER_CANCEL.format(
            amount=payment.amount if payment else "—",
            currency=payment.currency if payment else "—",
            tariff_name=tariff_name,
            payment_id=payment_id,
        )

        builder = InlineKeyboardBuilder()
        builder.button(
            text="💬 Написать в поддержку",
            url=f"https://t.me/{support_username}",
        )
        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )
        builder.adjust(1, 1)

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            builder.as_markup(),
        )

    elif result_code == "manual_review":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")

        builder = InlineKeyboardBuilder()
        builder.button(
            text="💬 Написать в поддержку",
            url=f"https://t.me/{support_username}",
        )
        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )
        builder.adjust(1, 1)

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_MANUAL_REVIEW_TEXT,
            builder.as_markup(),
        )

    elif result_code == "api_error":
        await callback.answer(
            texts.PAYMENT_API_ERROR,
            show_alert=True,
        )

    elif result_code == "refunded":
        await callback.answer(
            texts.PAYMENT_REFUNDED_SHORT,
            show_alert=True,
        )

    elif result_code == "cancelled":
        await callback.answer(
            texts.PAYMENT_CANCELLED_SHORT,
            show_alert=True,
        )

    else:
        await callback.answer(
            texts.PAYMENT_NOT_RECEIVED,
            show_alert=True,
        )