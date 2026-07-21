import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from database.repositories.payments_repo import get_payment_by_id
from services.payment_service import PaymentService
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name

from .common import (
    MANUAL_GRANT_ALLOWED_STATUSES,
    PAYMENT_STATUS_NAMES,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_manual_grant:"))
async def admin_manual_grant(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment_id = int(callback.data.split(":")[1])

    payment = await get_payment_by_id(session, payment_id)

    if not payment:
        await callback.answer(
            "❌ Платёж не найден",
            show_alert=True,
        )
        return

    if payment.status == "completed":
        await callback.answer(
            "❌ Платёж уже выдан",
            show_alert=True,
        )
        return

    if payment.status == "refunded":
        await callback.answer(
            "❌ Платёж возвращён, выдача запрещена",
            show_alert=True,
        )
        return

    if payment.status not in MANUAL_GRANT_ALLOWED_STATUSES:
        await callback.answer(
            "❌ Недопустимый статус платежа",
            show_alert=True,
        )
        return

    user = payment.user

    if not user:
        await callback.answer(
            "❌ Пользователь не найден",
            show_alert=True,
        )
        return

    if user.is_deleted:
        await callback.answer(
            "❌ Пользователь удалён",
            show_alert=True,
        )
        return

    if user.is_banned:
        await callback.answer(
            "❌ Пользователь заблокирован. "
            "Сначала разблокируйте пользователя.",
            show_alert=True,
        )
        return

    tariff = payment.tariff

    tariff_name = (
        get_tariff_display_name(
            getattr(tariff, "device_limit", 2)
        )
        if tariff
        else "—"
    )

    status_name = PAYMENT_STATUS_NAMES.get(
        payment.status,
        payment.status,
    )

    text = (
        f"⚠️ <b>Подтверждение ручной выдачи</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment.id}</code>\n"
        f"👤 <b>Клиент:</b> <code>{user.telegram_id}</code>\n"
        f"💎 <b>Тариф:</b> {tariff_name}\n"
        f"💰 <b>Сумма:</b> {payment.amount} {payment.currency}\n"
        f"📦 <b>Статус:</b> {status_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Подписка будет выдана вручную. "
        f"Клиент получит уведомление.</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=(
                    f"admin_manual_grant_apply:{payment.id}"
                ),
                cancel_callback="admin_menu",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_manual_grant edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_manual_grant_apply:"))
async def admin_manual_grant_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    payment_id = int(callback.data.split(":")[1])

    try:
        success, result = await PaymentService.force_grant_payment(
            session,
            payment_id,
            callback.from_user.id,
        )

        if success:
            payment = await get_payment_by_id(
                session,
                payment_id,
            )

            user_tg_id = (
                payment.user.telegram_id
                if payment and payment.user
                else "—"
            )

            await callback.answer(
                f"✅ Подписка выдана вручную для {user_tg_id}",
                show_alert=True,
            )

            try:
                await callback.message.edit_text(
                    f"✅ <b>Подписка выдана вручную</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💳 <b>Платёж ID:</b> <code>{payment_id}</code>\n"
                    f"👤 <b>Клиент:</b> <code>{user_tg_id}</code>\n"
                    f"🛠 <b>Админ:</b> <code>{callback.from_user.id}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"<i>Клиент получил доступ автоматически.</i>",
                    parse_mode="HTML",
                )
            except TelegramBadRequest as e:
                logger.debug(
                    f"admin_manual_grant_apply edit_text failed: {e}"
                )

            try:
                from services.workers.heartbeat import get_bot_ref

                bot = get_bot_ref()

                if bot and payment and payment.user:
                    user = payment.user
                    tariff = payment.tariff

                    tariff_name = (
                        get_tariff_display_name(
                            getattr(tariff, "device_limit", 2)
                        )
                        if tariff
                        else "—"
                    )

                    valid_until = format_datetime(
                        user.subscription_end
                    )

                    client_msg = (
                        f"✅ <b>Доступ активирован</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"💎 <b>Тариф:</b> {tariff_name}\n"
                        f"📅 <b>Действует до:</b> {valid_until}\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"Спасибо за ожидание. Доступ уже активен."
                    )

                    builder = InlineKeyboardBuilder()

                    builder.button(
                        text="🔌 Подключить устройство",
                        callback_data="menu_connections",
                    )

                    builder.button(
                        text="🏠 В главное меню",
                        callback_data="back_to_main_menu",
                    )

                    builder.adjust(1, 1)

                    await bot.send_message(
                        user.telegram_id,
                        client_msg,
                        reply_markup=builder.as_markup(),
                        parse_mode="HTML",
                    )

            except Exception as notify_error:
                logger.error(
                    "Failed to notify client after manual grant: "
                    f"{notify_error}"
                )

        else:
            await callback.answer(
                f"❌ Ошибка: {result}",
                show_alert=True,
            )

    except Exception as e:
        logger.error(
            f"admin_manual_grant_apply error: {e}",
            exc_info=True,
        )

        await callback.answer(
            "❌ Ошибка при выдаче подписки",
            show_alert=True,
        )