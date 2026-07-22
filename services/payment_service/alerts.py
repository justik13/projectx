import logging

from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import get_settings

from .common import (
    MANUAL_REVIEW_REASONS,
    _alerted_manual_review,
    _alerted_paid_after_cancel,
    _alerted_payment_not_found,
    _notified_paid_after_cancel,
)

logger = logging.getLogger(__name__)


async def _send_alert_to_admins(
    message: str,
    keyboard=None,
) -> bool:
    """
    Отправляет алерт админам.

    Возвращает True, если отправлен хотя бы одному админу.
    """
    from services.workers.heartbeat import get_bot_ref

    bot = get_bot_ref()

    if bot is None:
        logger.error(
            "Admin alert SKIPPED: bot_ref is None. "
            "Message: %s",
            message[:200],
        )
        return False

    settings = get_settings()
    admin_ids = settings.ADMIN_IDS

    if not admin_ids:
        return False

    sent = False

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                message,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            sent = True
        except Exception as e:
            logger.error(
                "Failed to send admin alert to %s: %s",
                admin_id,
                e,
            )

    return sent


async def _send_manual_review_alert_now(
    snapshot: dict,
    reason: str,
    source: str,
) -> None:
    """
    Отправляет алерт о платеже, который требует ручной проверки.
    """
    payment_id = snapshot.get("payment_id")

    if payment_id is None:
        return

    alert_key = payment_id

    if alert_key in _alerted_manual_review:
        return

    reason_text = MANUAL_REVIEW_REASONS.get(reason, reason)

    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Выдать подписку",
        callback_data=f"admin_manual_grant:{payment_id}",
    )

    user_telegram_id = snapshot.get("user_telegram_id")

    builder.button(
        text="👤 Профиль клиента",
        callback_data=(
            f"admin_user_card:{user_telegram_id}"
            if user_telegram_id
            else "admin_menu"
        ),
    )

    builder.adjust(1, 1)

    keyboard = builder.as_markup()

    message = (
        f"⚠️ <b>Платёж требует ручной проверки</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment_id}</code>\n"
        f"👤 <b>Клиент:</b> "
        f"<code>{user_telegram_id or '—'}</code> "
        f"({snapshot.get('username', '—')})\n"
        f"💎 <b>Тариф:</b> {snapshot.get('tariff_name', '—')}\n"
        f"💰 <b>Сумма:</b> {snapshot.get('amount', '—')} "
        f"{snapshot.get('currency', '—')}\n"
        f"🧩 <b>Причина:</b> {reason_text}\n"
        f"📍 <b>Источник:</b> <code>{source}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Доступ не выдан автоматически.</i>"
    )

    sent = await _send_alert_to_admins(message, keyboard)

    if sent:
        _alerted_manual_review[alert_key] = True


async def _send_paid_after_cancel_alert_now(
    snapshot: dict,
) -> None:
    """
    Отправляет админам алерт о ситуации, когда оплата пришла
    после отмены платежа.
    """
    payment_id = snapshot.get("payment_id")

    if payment_id is None:
        return

    if payment_id in _alerted_paid_after_cancel:
        return

    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Выдать подписку",
        callback_data=f"admin_manual_grant:{payment_id}",
    )

    user_telegram_id = snapshot.get("user_telegram_id")

    builder.button(
        text="👤 Профиль клиента",
        callback_data=(
            f"admin_user_card:{user_telegram_id}"
            if user_telegram_id
            else "admin_menu"
        ),
    )

    builder.adjust(1, 1)

    keyboard = builder.as_markup()

    message = (
        f"⚠️ <b>Оплата после отмены</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment_id}</code>\n"
        f"👤 <b>Клиент:</b> "
        f"<code>{user_telegram_id or '—'}</code> "
        f"({snapshot.get('username', '—')})\n"
        f"💎 <b>Тариф:</b> {snapshot.get('tariff_name', '—')}\n"
        f"💰 <b>Сумма:</b> {snapshot.get('amount', '—')} "
        f"{snapshot.get('currency', '—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Деньги поступили, но платёж был ранее отменён.\n"
        f"Клиент уведомлён автоматически.\n"
        f"Выберите действие:</i>"
    )

    sent = await _send_alert_to_admins(message, keyboard)

    if sent:
        _alerted_paid_after_cancel[payment_id] = True


async def _notify_client_paid_after_cancel_now(
    snapshot: dict,
) -> None:
    """
    Уведомляет клиента, что оплата получена, но платёж был ранее
    отменён, поэтому доступ не выдан автоматически.
    """
    payment_id = snapshot.get("payment_id")
    user_telegram_id = snapshot.get("user_telegram_id")

    if payment_id is None or user_telegram_id is None:
        return

    if payment_id in _notified_paid_after_cancel:
        return

    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    from database.connection import session_scope
    from database.repositories.users_repo import (
        mark_user_bot_blocked,
    )
    from services.workers.heartbeat import get_bot_ref

    bot = get_bot_ref()

    if bot is None:
        logger.error(
            "Client notification SKIPPED: bot_ref is None. "
            "Payment %s",
            payment_id,
        )
        return

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

    keyboard = builder.as_markup()

    message = (
        f"💳 <b>Мы получили вашу оплату</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Сумма:</b> {snapshot.get('amount', '—')} "
        f"{snapshot.get('currency', '—')}\n"
        f"💎 <b>Тариф:</b> {snapshot.get('tariff_name', '—')}\n"
        f"🆔 <b>Платёж:</b> <code>{payment_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Ранее в боте была нажата кнопка «Отменить», "
        f"поэтому доступ не активировался автоматически.\n"
        f"Напишите нам — решим за 2 минуты."
    )

    try:
        await bot.send_message(
            user_telegram_id,
            message,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        _notified_paid_after_cancel[payment_id] = True

        logger.info(
            "Paid-after-cancel notification sent to user %s "
            "for payment %s",
            user_telegram_id,
            payment_id,
        )

    except TelegramForbiddenError:
        _notified_paid_after_cancel[payment_id] = True

        logger.info(
            "Paid-after-cancel notification: user %s blocked the bot",
            user_telegram_id,
        )

        try:
            async with session_scope() as session:
                await mark_user_bot_blocked(
                    session,
                    user_telegram_id,
                )
        except Exception as e:
            logger.error(
                "Failed to mark user %s as bot_blocked: %s",
                user_telegram_id,
                e,
            )

    except Exception as e:
        logger.error(
            "Failed to send paid-after-cancel notification to "
            "user %s: %s",
            user_telegram_id,
            e,
        )


async def _send_cancel_after_completed_alert_now(
    snapshot: dict,
    transaction_id: str,
) -> None:
    """
    Критический алерт: пришёл CANCELED по уже completed платежу.
    """
    builder = InlineKeyboardBuilder()

    payment_id = snapshot.get("payment_id")
    user_telegram_id = snapshot.get("user_telegram_id")

    builder.button(
        text="👤 Профиль клиента",
        callback_data=(
            f"admin_user_card:{user_telegram_id}"
            if user_telegram_id
            else "admin_menu"
        ),
    )

    builder.adjust(1)

    keyboard = builder.as_markup()

    message = (
        f"🚨 <b>Критическая платёжная ситуация</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment_id}</code>\n"
        f"👤 <b>Клиент:</b> "
        f"<code>{user_telegram_id or '—'}</code> "
        f"({snapshot.get('username', '—')})\n"
        f"💎 <b>Тариф:</b> {snapshot.get('tariff_name', '—')}\n"
        f"💰 <b>Сумма:</b> {snapshot.get('amount', '—')} "
        f"{snapshot.get('currency', '—')}\n"
        f"🔗 <b>Transaction:</b> <code>{transaction_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Платёж уже был completed, но пришёл CANCELED.\n"
        f"Требуется ручная проверка. Возможна отмена/chargeback.</i>"
    )

    await _send_alert_to_admins(message, keyboard)


async def _send_chargeback_alert_now(
    snapshot: dict,
    transaction_id: str,
) -> None:
    """
    Отправляет админам алерт о chargeback.
    """
    builder = InlineKeyboardBuilder()

    payment_id = snapshot.get("payment_id")
    user_telegram_id = snapshot.get("user_telegram_id")

    builder.button(
        text="👤 Профиль пользователя",
        callback_data=(
            f"admin_user_card:{user_telegram_id}"
            if user_telegram_id
            else "admin_menu"
        ),
    )

    builder.adjust(1)

    keyboard = builder.as_markup()

    message = (
        f"🚨 <b>Возврат средств</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment_id}</code>\n"
        f"👤 <b>Пользователь:</b> "
        f"<code>{user_telegram_id or '—'}</code> "
        f"({snapshot.get('username', '—')})\n"
        f"💎 <b>Тариф:</b> {snapshot.get('tariff_name', '—')}\n"
        f"💰 <b>Сумма:</b> {snapshot.get('amount', '—')} "
        f"{snapshot.get('currency', '—')}\n"
        f"🔗 <b>Transaction:</b> <code>{transaction_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Доступ отозван. Устройства удалены.\n"
        f"Реферальные бонусы откатаны.</i>"
    )

    await _send_alert_to_admins(message, keyboard)


async def _send_payment_not_found_alert_now(
    snapshot: dict,
) -> None:
    """
    Отправляет админам алерт, если платёж не найден
    или не может быть сопоставлен с пользователем.
    """
    transaction_id = snapshot.get("transaction_id") or "—"
    source = snapshot.get("source") or "unknown"
    status = snapshot.get("status") or "unknown"
    user_telegram_id = snapshot.get("user_telegram_id") or "—"

    alert_key = (
        f"{source}:{transaction_id}:{status}:{user_telegram_id}"
    )

    if alert_key in _alerted_payment_not_found:
        return

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🛠 В админку",
        callback_data="admin_menu",
    )

    builder.adjust(1)

    keyboard = builder.as_markup()

    message = (
        f"🚨 <b>Платёж не найден / не сопоставлен</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 <b>Transaction / payload:</b> "
        f"<code>{transaction_id}</code>\n"
        f"📦 <b>Статус события:</b> <code>{status}</code>\n"
        f"👤 <b>Telegram ID:</b> "
        f"<code>{user_telegram_id}</code>\n"
        f"📍 <b>Источник:</b> <code>{source}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Проверьте платёж вручную.</i>"
    )

    sent = await _send_alert_to_admins(message, keyboard)

    if sent:
        _alerted_payment_not_found[alert_key] = True