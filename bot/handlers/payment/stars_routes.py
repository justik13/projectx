import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_payment_method_keyboard,
    get_payment_success_keyboard,
)
from config.settings import get_settings
from database.connection import session_scope
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_simple,
    mark_payment_as_cancelled,
)
from database.repositories.profiles_repo import get_user_profiles
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.maintenance_service import MaintenanceService
from services.payment_service import PaymentService
from services.payment_service.alerts import (
    _send_payment_not_found_alert_now,
)
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub, send_hub_invoice

from .common import (
    PAYMENT_MANUAL_REVIEW_TEXT,
    _check_tariff_change_allowed,
    _is_subscription_active,
    _render_maintenance,
    _show_hub,
    _show_showcase,
    _to_decimal,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
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

    tariff = await get_tariff_by_id(session, tariff_id)

    # ИСПРАВЛЕНО: раньше был тихий return.
    # Теперь пользователь видит понятную ошибку.
    if not tariff:
        await callback.answer(
            texts.ERROR_TARIFF_NOT_FOUND,
            show_alert=True,
        )
        return

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
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
        await callback.answer()
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
        await callback.answer()
        return

    try:
        await callback.answer("💳 Отправляю инвойс...")

        payment = await create_payment(
            session=session,
            user_id=db_user.id,
            tariff_id=tariff.id,
            amount=Decimal(str(tariff.price_stars)),
            currency="stars",
        )

        try:
            invoice_builder = InlineKeyboardBuilder()
            invoice_builder.row(
                InlineKeyboardButton(
                    text="💳 Оплатить",
                    pay=True,
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=(
                        f"cancel_invoice:"
                        f"{payment.id}:{tariff.id}:{source}"
                    ),
                ),
            )

            device_limit = getattr(tariff, "device_limit", 2)
            await send_hub_invoice(
                callback.bot,
                callback.message.chat.id,
                title=(
                    f"Доступ на {tariff.duration_days} дней "
                    f"({device_limit} устр.)"
                ),
                description=(
                    "Оплата цифрового доступа к защищённым "
                    "конфигурациям сети."
                ),
                prices=[
                    LabeledPrice(
                        label="Доступ к сети",
                        amount=tariff.price_stars,
                    )
                ],
                provider_token="",
                payload=f"stars_payment:{payment.id}",
                currency="XTR",
                start_parameter="network-access-stars",
                reply_markup=invoice_builder.as_markup(),
            )

            await state.update_data(
                tariff_id=tariff.id,
                payment_id=payment.id,
            )

        except TelegramAPIError as e:
            logger.error(f"Failed to send invoice: {e}")
            try:
                await render_hub(
                    callback.bot,
                    callback.message.chat.id,
                    texts.ERROR_PAYMENT_SERVICE,
                    get_back_button(back_callback),
                )
            except Exception as render_error:
                logger.error(
                    "Failed to render error hub after invoice "
                    "failure: %s",
                    render_error,
                )
            payment.status = "failed"

    except Exception as e:
        logger.error(f"pay_stars error: {e}", exc_info=True)
        await callback.answer(
            "❌ Ошибка при создании платежа",
            show_alert=True,
        )


@router.pre_checkout_query()
async def process_pre_checkout(
    pre_checkout_query: PreCheckoutQuery,
    session: AsyncSession | None = None,
) -> None:
    async def _validate(
        pre_checkout_query: PreCheckoutQuery,
        db_session: AsyncSession,
    ):
        payload = pre_checkout_query.invoice_payload
        if not payload or not payload.startswith("stars_payment:"):
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный платёж",
            )
            return

        try:
            payment_id = int(payload.split(":")[1])
        except (ValueError, IndexError):
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный идентификатор платежа",
            )
            return

        payment = await get_payment_by_id(db_session, payment_id)
        if not payment:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Платёж не найден",
            )
            return

        if payment.status != "pending":
            if payment.status == "completed":
                await pre_checkout_query.answer(
                    ok=False,
                    error_message="Платёж уже обработан",
                )
            elif payment.status == "cancelled":
                await pre_checkout_query.answer(
                    ok=False,
                    error_message="Платёж отменён",
                )
            else:
                await pre_checkout_query.answer(
                    ok=False,
                    error_message="Платёж недоступен",
                )
            return

        if (
            not payment.user
            or payment.user.telegram_id
            != pre_checkout_query.from_user.id
        ):
            await pre_checkout_query.answer(
                ok=False,
                error_message="Нет доступа к этому платежу",
            )
            return

        if payment.user.is_banned or payment.user.is_deleted:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Доступ недоступен",
            )
            return

        if not payment.tariff or not payment.tariff.is_active:
            await pre_checkout_query.answer(
                ok=False,
                error_message="Тариф недоступен",
            )
            return

        if not await MaintenanceService.can_user_perform_action(
            db_session,
            pre_checkout_query.from_user.id,
        ):
            await pre_checkout_query.answer(
                ok=False,
                error_message=(
                    "Ведутся технические работы. "
                    "Попробуйте позже."
                ),
            )
            return

        if payment.currency != "stars":
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректный способ оплаты",
            )
            return

        expected_amount = Decimal(str(payment.tariff.price_stars))
        if payment.amount != expected_amount:
            logger.error(
                "Pre-checkout amount mismatch: payment=%s, "
                "expected=%s, payment_id=%s",
                payment.amount,
                expected_amount,
                payment_id,
            )
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная сумма платежа",
            )
            return

        telegram_amount = _to_decimal(
            pre_checkout_query.total_amount
        )
        if (
            telegram_amount is None
            or telegram_amount != expected_amount
        ):
            logger.error(
                "Pre-checkout Telegram amount mismatch: "
                "telegram=%s, expected=%s, payment_id=%s",
                telegram_amount,
                expected_amount,
                payment_id,
            )
            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная сумма платежа",
            )
            return

        await pre_checkout_query.answer(ok=True)

    if session is not None:
        await _validate(pre_checkout_query, session)
    else:
        async with session_scope() as fallback_session:
            await _validate(pre_checkout_query, fallback_session)


@router.message(F.successful_payment)
async def process_successful_payment(
    message: Message,
    state: FSMContext,
    session: AsyncSession = None,
) -> None:
    if session is None:
        logger.error(
            "process_successful_payment: session is None, "
            "cannot process payment for user %s",
            message.from_user.id if message.from_user else "?",
        )
        return

    await state.clear()

    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_payment:"):
        return

    try:
        payment_id = int(payload.split(":")[1])
    except (ValueError, IndexError):
        return

    payment = await get_payment_by_id(session, payment_id)
    if not payment:
        try:
            await _send_payment_not_found_alert_now(
                {
                    "transaction_id": payload,
                    "status": "successful_payment_not_found",
                    "source": "stars_successful_payment",
                    "user_telegram_id": message.from_user.id,
                }
            )
        except Exception as e:
            logger.error(
                "Failed to send payment not found alert: %s",
                e,
            )
        await render_hub(
            message.bot,
            message.chat.id,
            texts.PAYMENT_DELAYED,
            get_back_button("menu_subscription"),
        )
        return

    if (
        not payment.user
        or payment.user.telegram_id != message.from_user.id
    ):
        try:
            await _send_payment_not_found_alert_now(
                {
                    "transaction_id": payload,
                    "status": "successful_payment_owner_mismatch",
                    "source": "stars_successful_payment",
                    "user_telegram_id": message.from_user.id,
                }
            )
        except Exception as e:
            logger.error(
                "Failed to send payment owner mismatch alert: %s",
                e,
            )
        await render_hub(
            message.bot,
            message.chat.id,
            texts.PAYMENT_DELAYED,
            get_back_button("menu_subscription"),
        )
        return

    success, result_code = (
        await PaymentService.handle_successful_payment(
            session,
            payment_id,
        )
    )

    if success and result_code in ("success", "already_processed"):
        user = await get_user_by_telegram_id(
            session,
            message.from_user.id,
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
        device_limit = (
            getattr(payment.tariff, "device_limit", 2)
            if payment.tariff
            else 2
        )
        tariff_name = get_tariff_display_name(device_limit)

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
            message.bot,
            message.chat.id,
            text,
            get_payment_success_keyboard(),
        )

    elif success and result_code == "paid_after_cancel":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")
        device_limit = (
            getattr(payment.tariff, "device_limit", 2)
            if payment.tariff
            else 2
        )
        tariff_name = get_tariff_display_name(device_limit)
        text = texts.PAYMENT_PAID_AFTER_CANCEL.format(
            amount=payment.amount,
            currency=payment.currency,
            tariff_name=tariff_name,
            payment_id=payment.id,
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
            message.bot,
            message.chat.id,
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
            message.bot,
            message.chat.id,
            PAYMENT_MANUAL_REVIEW_TEXT,
            builder.as_markup(),
        )

    else:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.PAYMENT_DELAYED,
            get_back_button("menu_subscription"),
        )


@router.callback_query(F.data.startswith("cancel_invoice:"))
async def cancel_invoice(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
    db_user=None,
) -> None:
    parts = callback.data.split(":")
    try:
        payment_id = int(parts[1])
        tariff_id = int(parts[2])
        source = parts[3] if len(parts) > 3 else "showcase"
    except (ValueError, IndexError):
        await callback.answer(
            "Некорректный платёж",
            show_alert=True,
        )
        return

    if not db_user or not session:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment = await get_payment_by_id_simple(session, payment_id)
    if not payment:
        await callback.answer(
            "Платёж не найден",
            show_alert=True,
        )
        return

    if payment.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    if payment.status == "completed":
        await callback.answer(
            "Платёж уже обработан",
            show_alert=True,
        )
        return

    try:
        await mark_payment_as_cancelled(session, payment_id)
    except Exception as e:
        logger.warning(f"Failed to cancel payment {payment_id}: {e}")

    await state.clear()
    await callback.answer("❌ Инвойс отменен")

    tariff = await get_tariff_by_id(session, tariff_id)

    # ИСПРАВЛЕНО: проверка tariff.is_active.
    # Если тариф отключён, показываем витрину/хаб вместо checkout.
    if tariff and tariff.is_active:
        device_limit = getattr(tariff, "device_limit", 2)
        tariff_name = get_tariff_display_name(device_limit)
        text = texts.PAYMENT_CHECKOUT_TEXT.format(
            tariff_name=tariff_name,
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars,
        )
        settings = get_settings()
        sbp_enabled = bool(
            settings.PLATEGA_MERCHANT_ID and settings.PLATEGA_SECRET
        )
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_payment_method_keyboard(
                tariff.id,
                device_limit,
                sbp_enabled=sbp_enabled,
                source=source,
            ),
        )
        return

    # Тариф не найден или отключён — показываем витрину/хаб
    user = await get_user_by_telegram_id(
        session,
        callback.from_user.id,
    )
    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)