import logging
from decimal import Decimal, InvalidOperation

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
    get_change_tariff_keyboard,
    get_payment_method_keyboard,
    get_payment_success_keyboard,
    get_renew_keyboard,
    get_sbp_payment_keyboard,
    get_tariff_duration_keyboard,
    get_tariff_showcase_keyboard,
)
from config.settings import get_settings
from database.connection import session_scope
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_simple,
    mark_payment_as_cancelled,
)
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.maintenance_service import MaintenanceService
from services.payment_service import PaymentService
from utils.datetime_helpers import is_expired
from utils.formatters import format_datetime, format_days_left
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub, safe, send_hub_invoice

router = Router()
logger = logging.getLogger(__name__)


PAYMENT_MANUAL_REVIEW_TEXT = (
    "💳 <b>Оплата получена</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Мы проверяем платёж.\n"
    "Если доступ не активировался в течение нескольких минут, "
    "напишите в поддержку.\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<i>Обычно проверка занимает не более 5 минут.</i>"
)


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def _is_subscription_active(user) -> bool:
    if not user or not user.subscription_end:
        return False

    return not is_expired(user.subscription_end)


async def _render_maintenance(
    callback: CallbackQuery,
    session: AsyncSession,
    *,
    back_to: str = "back_to_main_menu",
) -> None:
    message = await MaintenanceService.get_message(session)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        message,
        get_back_button(back_to),
    )


@router.callback_query(F.data.in_(["menu_buy", "menu_subscription"]))
async def hub_menu_payment(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    session: AsyncSession = None,
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


async def _show_showcase(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    tariffs = await get_active_tariffs(session)

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("back_to_main_menu"),
        )
        return

    grouped: dict[int, list] = {}

    for tariff in tariffs:
        limit = getattr(tariff, "device_limit", 2)

        if limit not in grouped:
            grouped[limit] = []

        grouped[limit].append(tariff)

    keyboard = get_tariff_showcase_keyboard(grouped)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_SHOWCASE_HEADER,
        keyboard,
    )


async def _show_hub(
    callback: CallbackQuery,
    user,
    session: AsyncSession,
) -> None:
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

    builder.button(
        text="🔄 Продлить доступ",
        callback_data="payment_quick_renew",
    )

    builder.button(
        text="⚙️ Сменить тариф",
        callback_data="payment_change_tariff",
    )

    builder.button(
        text="👤 Профиль",
        callback_data="menu_profile",
    )

    builder.button(
        text="🏠 В главное меню",
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )


@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await callback.answer()
    await _show_showcase(callback, session)


@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await callback.answer()

    device_limit = int(callback.data.split(":")[1])

    tariffs = await get_active_tariffs(session)

    type_tariffs = [
        tariff
        for tariff in tariffs
        if getattr(tariff, "device_limit", 2) == device_limit
    ]

    if not type_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("payment_showcase"),
        )
        return

    description = texts.PAYMENT_TARIFF_DESCRIPTION.get(
        device_limit,
        "",
    )

    text = description + texts.PAYMENT_DURATION_HEADER

    keyboard = get_tariff_duration_keyboard(type_tariffs)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(
    callback: CallbackQuery,
    db_user,
    session: AsyncSession,
) -> None:
    await callback.answer()

    tariffs = await get_active_tariffs(session)

    current_limit = db_user.device_limit

    renew_tariffs = [
        tariff
        for tariff in tariffs
        if getattr(tariff, "device_limit", 2) == current_limit
    ]

    if not renew_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    tariff_name = get_tariff_display_name(current_limit)

    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_renew_keyboard(renew_tariffs)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(
    callback: CallbackQuery,
    db_user,
    session: AsyncSession,
) -> None:
    await callback.answer()

    tariffs = await get_active_tariffs(session)

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    current_limit = db_user.device_limit

    tariff_name = get_tariff_display_name(current_limit)

    is_active = await _is_subscription_active(db_user)

    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_change_tariff_keyboard(
        tariffs,
        current_limit,
        is_subscription_active=is_active,
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    session: AsyncSession = None,
) -> None:
    tariff_id = int(callback.data.split(":")[1])

    tariff = await get_tariff_by_id(session, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE,
            show_alert=True,
        )
        return

    device_limit = getattr(tariff, "device_limit", 2)

    if db_user:
        profiles_count = await get_user_profiles_count(
            session,
            db_user.id,
        )

        if profiles_count > device_limit:
            text = texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                profiles_count=profiles_count,
                new_limit=device_limit,
            )

            await render_hub(
                callback.bot,
                callback.message.chat.id,
                text,
                get_back_button("payment_change_tariff"),
            )

            await callback.answer()
            return

    tariff_name = get_tariff_display_name(device_limit)

    text = texts.PAYMENT_CHECKOUT_TEXT.format(
        tariff_name=tariff_name,
        duration_days=tariff.duration_days,
        price_rub=tariff.price_rub,
        price_stars=tariff.price_stars,
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_payment_method_keyboard(tariff.id, device_limit),
    )

    await callback.answer()


@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    session: AsyncSession = None,
) -> None:
    try:
        await callback.answer("💳 Отправляю инвойс...")

        tariff_id = int(callback.data.split(":")[1])

        tariff = await get_tariff_by_id(session, tariff_id)

        if not tariff or not db_user:
            return

        if not tariff.is_active:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_TARIFF_UNAVAILABLE,
                get_back_button(f"select_tariff:{tariff_id}"),
            )
            return

        if not await MaintenanceService.can_user_perform_action(
            session,
            callback.from_user.id,
        ):
            await _render_maintenance(
                callback,
                session,
                back_to=f"select_tariff:{tariff_id}",
            )
            return

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
                        f"cancel_invoice:{payment.id}:{tariff.id}"
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

            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_PAYMENT_SERVICE,
                get_back_button(f"select_tariff:{tariff_id}"),
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
    """
    Полная серверная валидация перед оплатой Stars.

    Проверяется:
    - платёж существует;
    - платёж находится в статусе pending;
    - владелец платежа совпадает;
    - пользователь не забанен и не удалён;
    - тариф существует и активен;
    - режим технических работ не включён;
    - сумма платежа совпадает с тарифом.
    """

    async def _validate(pre_checkout_query: PreCheckoutQuery, db_session: AsyncSession):
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
            or payment.user.telegram_id != pre_checkout_query.from_user.id
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
                "Pre-checkout amount mismatch: payment=%s, expected=%s, "
                "payment_id=%s",
                payment.amount,
                expected_amount,
                payment_id,
            )

            await pre_checkout_query.answer(
                ok=False,
                error_message="Некорректная сумма платежа",
            )
            return

        telegram_amount = _to_decimal(pre_checkout_query.total_amount)

        if telegram_amount is None or telegram_amount != expected_amount:
            logger.error(
                "Pre-checkout Telegram amount mismatch: telegram=%s, "
                "expected=%s, payment_id=%s",
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

        profiles = await get_user_profiles(session, user.id)

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

    if tariff:
        device_limit = getattr(tariff, "device_limit", 2)

        tariff_name = get_tariff_display_name(device_limit)

        text = texts.PAYMENT_CHECKOUT_TEXT.format(
            tariff_name=tariff_name,
            duration_days=tariff.duration_days,
            price_rub=tariff.price_rub,
            price_stars=tariff.price_stars,
        )

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            text,
            get_payment_method_keyboard(tariff.id, device_limit),
        )

        return

    user = await get_user_by_telegram_id(
        session,
        callback.from_user.id,
    )

    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)


@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
) -> None:
    try:
        await callback.answer("⏳ Создаю платеж...")

        tariff_id = int(callback.data.split(":")[1])

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
                get_back_button(f"select_tariff:{tariff_id}"),
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

        if not await MaintenanceService.can_user_perform_action(
            session,
            callback.from_user.id,
        ):
            await _render_maintenance(
                callback,
                session,
                back_to=f"select_tariff:{tariff_id}",
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
                get_back_button(f"select_tariff:{tariff_id}"),
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
            ),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"pay_sbp error: {e}", exc_info=True)

        await callback.answer(
            "❌ Ошибка при создании платежа",
            show_alert=True,
        )


@router.callback_query(F.data.startswith("check_payment:"))
async def check_payment_status(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
    db_user=None,
) -> None:
    await callback.answer("⏳ Проверяю статус...")

    try:
        payment_id = int(callback.data.split(":")[1])
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

    payment_simple = await get_payment_by_id_simple(
        session,
        payment_id,
    )

    if not payment_simple:
        await callback.answer(
            "Платёж не найден",
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

        profiles = await get_user_profiles(session, user.id)

        valid_until = (
            format_datetime(user.subscription_end)
            if user and user.subscription_end
            else "—"
        )

        device_limit = (
            getattr(payment.tariff, "device_limit", 2)
            if payment and payment.tariff
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
            callback.bot,
            callback.message.chat.id,
            text,
            get_payment_success_keyboard(),
        )

    elif result_code == "paid_after_cancel":
        settings = get_settings()
        support_username = settings.SUPPORT_USERNAME.lstrip("@")

        payment = await get_payment_by_id(session, payment_id)

        device_limit = (
            getattr(payment.tariff, "device_limit", 2)
            if payment and payment.tariff
            else 2
        )

        tariff_name = get_tariff_display_name(device_limit)

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
            PAYMENT_MANUAL_REVIEW_TEXT,
            builder.as_markup(),
        )

    elif result_code == "api_error":
        await callback.answer(
            "⚠️ Не удалось связаться с платёжной системой. "
            "Попробуйте через минуту.",
            show_alert=True,
        )

    elif result_code == "refunded":
        await callback.answer(
            "❌ Платёж был возвращён.",
            show_alert=True,
        )

    else:
        await callback.answer(
            "❌ Платёж ещё не поступил. Попробуйте позже.",
            show_alert=True,
        )