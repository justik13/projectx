import logging
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import (
    get_back_button, get_payment_method_keyboard, get_tariff_showcase_keyboard,
    get_tariff_duration_keyboard, get_renew_keyboard, get_change_tariff_keyboard,
    get_payment_success_keyboard,
)
from bot import texts
from database.models import User
from database.repositories.payments_repo import create_payment, get_payment_by_id
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.profiles_repo import get_user_profiles
from services.payment_service import PaymentService
from utils.formatters import format_datetime, format_days_left
from utils.telegram import render_hub, send_hub_invoice

router = Router()
logger = logging.getLogger(__name__)


def _get_tariff_display_name(device_limit: int) -> str:
    if device_limit <= 2: return "📱 Для себя"
    elif device_limit <= 5: return "👨‍👩‍👧‍👦 Семейный"
    else: return "🚀 Pro"


async def _is_subscription_active(user: User) -> bool:
    if not user or not user.subscription_end: return False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return user.subscription_end > now


# ============================================================
# Вход в раздел оплаты
# ============================================================

@router.callback_query(F.data.in_(["menu_buy", "menu_subscription"]))
async def hub_menu_payment(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await callback.answer()
    await state.clear()
    if not db_user: return
    is_active = await _is_subscription_active(db_user)
    if is_active:
        await _show_hub(callback, db_user, session)
    else:
        await _show_showcase(callback, session)


async def _show_showcase(callback: CallbackQuery, session: AsyncSession):
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_NO_TARIFFS, get_back_button("back_to_main_menu"))
        return
    grouped: dict[int, list] = {}
    for t in tariffs:
        limit = getattr(t, 'device_limit', 2)
        if limit not in grouped: grouped[limit] = []
        grouped[limit].append(t)
    kb = get_tariff_showcase_keyboard(grouped)
    await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_SHOWCASE_HEADER, kb)


async def _show_hub(callback: CallbackQuery, user: User, session: AsyncSession):
    profiles = await get_user_profiles(session, user.id)
    tariff_name = _get_tariff_display_name(user.device_limit)
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
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1, 1)
    await render_hub(callback.bot, callback.message.chat.id, text, builder.as_markup())


# ============================================================
# Выбор тарифа
# ============================================================

@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    await _show_showcase(callback, session)


@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(callback: CallbackQuery, session: AsyncSession):
    await callback.answer()
    device_limit = int(callback.data.split(":")[1])
    tariffs = await get_active_tariffs(session)
    type_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == device_limit]
    if not type_tariffs:
        await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_NO_TARIFFS, get_back_button("payment_showcase"))
        return
    desc = texts.PAYMENT_TARIFF_DESCRIPTION.get(device_limit, "")
    text = desc + texts.PAYMENT_DURATION_HEADER
    kb = get_tariff_duration_keyboard(type_tariffs)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)


@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(callback: CallbackQuery, db_user: User, session: AsyncSession):
    await callback.answer()
    tariffs = await get_active_tariffs(session)
    current_limit = db_user.device_limit
    renew_tariffs = [t for t in tariffs if getattr(t, 'device_limit', 2) == current_limit]
    if not renew_tariffs:
        await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_NO_TARIFFS, get_back_button("menu_subscription"))
        return
    tariff_name = _get_tariff_display_name(current_limit)
    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )
    kb = get_renew_keyboard(renew_tariffs)
    await render_hub(callback.bot, callback.message.chat.id, text, kb)


@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(callback: CallbackQuery, db_user: User, session: AsyncSession):
    await callback.answer()
    tariffs = await get_active_tariffs(session)
    if not tariffs:
        await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_NO_TARIFFS, get_back_button("menu_subscription"))
        return
    current_limit = db_user.device_limit
    tariff_name = _get_tariff_display_name(current_limit)
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
    db_user: User | None = None, session: AsyncSession = None,
):
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not tariff.is_active:
        await callback.answer(texts.ERROR_TARIFF_UNAVAILABLE, show_alert=True)
        return
    
    device_limit = getattr(tariff, 'device_limit', 2)
    
    # Блокировка даунгрейда во время активной подписки
    if db_user and await _is_subscription_active(db_user):
        current_limit = db_user.device_limit
        if device_limit < current_limit:
            text = texts.PAYMENT_DOWNGRADE_BLOCKED.format(
                current_limit=current_limit,
                new_limit=device_limit,
                valid_until=format_datetime(db_user.subscription_end),
            )
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button("payment_change_tariff"),
                parse_mode="HTML",
            )
            await callback.answer()
            return
    
    tariff_name = _get_tariff_display_name(device_limit)
    text = texts.PAYMENT_CHECKOUT_TEXT.format(
        tariff_name=tariff_name,
        duration_days=tariff.duration_days,
        price_rub=tariff.price_rub,
        price_stars=tariff.price_stars,
    )
    
    # ✅ ИСПРАВЛЕНО: Передаём device_limit для корректной кнопки "Назад"
    await callback.message.edit_text(
        text,
        reply_markup=get_payment_method_keyboard(tariff.id, device_limit),
        parse_mode="HTML",
    )
    await callback.answer()

# ============================================================
# Оплата Stars — с кнопкой отмены
# ============================================================

@router.callback_query(F.data.startswith("pay_stars:"))
async def pay_stars(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await callback.answer("💳 Отправляю инвойс...")
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff or not db_user: return

    payment = await create_payment(
        session=session, user_id=db_user.id,
        tariff_id=tariff.id, amount=tariff.price_stars, currency="stars",
    )

    try:
        # 1) Отправляем инвойс (заменяет текстовый хаб)
        invoice_msg_id = await send_hub_invoice(
            callback.bot, callback.message.chat.id,
            title=f"Подписка на {tariff.duration_days} дней ({getattr(tariff, 'device_limit', 2)} устр.)",
            description="Оплата цифрового доступа к защищенным конфигурациям сети.",
            prices=[LabeledPrice(label="Доступ к сети", amount=tariff.price_stars)],
            provider_token="",
            payload=f"stars_payment:{payment.id}",
            currency="XTR",
            start_parameter="network-access-stars",
        )

        # 2) ✅ Отправляем ОТДЕЛЬНОЕ сообщение с кнопкой отмены
        cancel_builder = InlineKeyboardBuilder()
        cancel_builder.button(text="❌ Отменить оплату", callback_data="cancel_invoice")
        cancel_builder.adjust(1)

        cancel_msg = await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text="💳 <b>Инвойс отправлен выше.</b>\nНажмите «Заплатить» в сообщении выше или отмените оплату.",
            reply_markup=cancel_builder.as_markup(),
            parse_mode="HTML",
        )

        # 3) Сохраняем ID обоих сообщений для последующего удаления
        await state.update_data(
            tariff_id=tariff.id,
            payment_id=payment.id,
            invoice_message_id=invoice_msg_id,
            cancel_message_id=cancel_msg.message_id,
        )

    except TelegramAPIError as e:
        logger.error(f"Failed to send invoice: {e}")
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.ERROR_PAYMENT_SERVICE,
            get_back_button(f"select_tariff:{tariff_id}")
        )
        payment.status = "failed"
        await session.commit()
        return


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, state: FSMContext, session: AsyncSession = None):
    """Обработка успешной оплаты Stars — удаляет инвойс, кнопку отмены и системное сообщение."""
    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    cancel_message_id = data.get("cancel_message_id")

    await state.clear()
    payload = message.successful_payment.invoice_payload
    if not payload.startswith("stars_payment:"): return

    payment_id = int(payload.split(":")[1])

    # ✅ Удаляем инвойс
    if invoice_message_id:
        try:
            await message.bot.delete_message(message.chat.id, invoice_message_id)
        except Exception:
            pass

    # ✅ Удаляем сообщение с кнопкой отмены
    if cancel_message_id:
        try:
            await message.bot.delete_message(message.chat.id, cancel_message_id)
        except Exception:
            pass

    # ✅ Удаляем системное сообщение Telegram об успешной оплате
    try:
        await message.delete()
    except Exception:
        pass

    if await PaymentService.handle_successful_payment(session, payment_id):
        user = await get_user_by_telegram_id(session, message.from_user.id)
        profiles = await get_user_profiles(session, user.id)
        payment = await get_payment_by_id(session, payment_id)
        valid_until = format_datetime(user.subscription_end) if user and user.subscription_end else "—"
        device_limit = getattr(payment.tariff, 'device_limit', 2) if payment.tariff else 2
        tariff_name = _get_tariff_display_name(device_limit)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(tariff_name=tariff_name, valid_until=valid_until)
            if profiles else texts.PAYMENT_SUCCESS_NEW.format(tariff_name=tariff_name, valid_until=valid_until)
        )
        await render_hub(message.bot, message.chat.id, text, get_payment_success_keyboard())
    else:
        await render_hub(message.bot, message.chat.id, texts.PAYMENT_DELAYED, get_back_button("menu_subscription"))


# ============================================================
# Отмена инвойса Stars
# ============================================================

@router.callback_query(F.data == "cancel_invoice")
async def cancel_invoice(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    """Отмена инвойса Stars — удаляет инвойс, помечает платёж как cancelled, возвращает к выбору оплаты."""
    await callback.answer("❌ Инвойс отменен")

    data = await state.get_data()
    invoice_message_id = data.get("invoice_message_id")
    cancel_message_id = data.get("cancel_message_id")
    tariff_id = data.get("tariff_id")
    payment_id = data.get("payment_id")

    # ✅ Удаляем инвойс Telegram
    if invoice_message_id:
        try:
            await callback.bot.delete_message(callback.from_user.id, invoice_message_id)
        except Exception:
            pass

    # ✅ Удаляем сообщение с кнопкой отмены
    if cancel_message_id:
        try:
            await callback.bot.delete_message(callback.from_user.id, cancel_message_id)
        except Exception:
            pass

    # ✅ Помечаем платёж как отменённый в БД
    if payment_id:
        try:
            from database.repositories.payments_repo import mark_payment_as_cancelled
            await mark_payment_as_cancelled(session, payment_id)
        except Exception as e:
            logger.warning(f"Failed to cancel payment {payment_id}: {e}")

    await state.clear()

    # Возвращаемся к экрану выбора способа оплаты
    if tariff_id:
        tariff = await get_tariff_by_id(session, tariff_id)
        if tariff:
            device_limit = getattr(tariff, 'device_limit', 2)
            tariff_name = _get_tariff_display_name(device_limit)
            text = texts.PAYMENT_CHECKOUT_TEXT.format(
                tariff_name=tariff_name,
                duration_days=tariff.duration_days,
                price_rub=tariff.price_rub,
                price_stars=tariff.price_stars,
            )
            await render_hub(
                callback.bot, callback.from_user.id,
                text, get_payment_method_keyboard(tariff.id, device_limit)
            )
            return

    # Fallback: возвращаем в меню подписки
    user = await get_user_by_telegram_id(session, callback.from_user.id)
    if user and await _is_subscription_active(user):
        await _show_hub(callback, user, session)
    else:
        await _show_showcase(callback, session)


# ============================================================
# Оплата СБП (заглушка)
# ============================================================

@router.callback_query(F.data.startswith("pay_sbp:"))
async def pay_sbp(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    await callback.answer()
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Оплатить", callback_data=f"confirm_payment_sbp:{tariff_id}")
    builder.button(text="← Назад", callback_data=f"select_tariff:{tariff_id}")
    builder.adjust(1)
    await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_SBP_TEXT.format(price_rub=tariff.price_rub), builder.as_markup())


@router.callback_query(F.data.startswith("confirm_payment_sbp:"))
async def confirm_payment_sbp(
    callback: CallbackQuery, state: FSMContext,
    db_user: User | None = None, session: AsyncSession = None,
):
    await callback.answer("⏳ Обрабатываю оплату...")
    await state.clear()
    if not db_user: return
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    payment = await create_payment(
        session=session, user_id=db_user.id,
        tariff_id=tariff.id, amount=tariff.price_rub, currency="rub",
    )
    if await PaymentService.handle_successful_payment(session, payment.id):
        fresh_user = await get_user_by_telegram_id(session, callback.from_user.id)
        profiles = await get_user_profiles(session, fresh_user.id)
        valid_until = format_datetime(fresh_user.subscription_end) if fresh_user and fresh_user.subscription_end else "—"
        device_limit = getattr(tariff, 'device_limit', 2)
        tariff_name = _get_tariff_display_name(device_limit)
        text = (
            texts.PAYMENT_SUCCESS_RENEW.format(tariff_name=tariff_name, valid_until=valid_until)
            if profiles else texts.PAYMENT_SUCCESS_NEW.format(tariff_name=tariff_name, valid_until=valid_until)
        )
        await render_hub(callback.bot, callback.message.chat.id, text, get_payment_success_keyboard())
    else:
        await render_hub(callback.bot, callback.message.chat.id, texts.PAYMENT_DELAYED, get_back_button("menu_subscription"))


@router.callback_query(F.data == "back_to_payment")
async def back_to_payment(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    await callback.answer()
    await state.clear()
    user = await get_user_by_telegram_id(session, callback.from_user.id)
    if user:
        await _show_hub(callback, user, session)
    else:
        await render_hub(callback.bot, callback.message.chat.id, texts.ERROR_OPERATION_CANCELLED, get_back_button("menu_subscription"))