import asyncio
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import redis.asyncio as aioredis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.middlewares.user_context import invalidate_user_cache
from config.settings import get_settings
from database.connection import queue_post_commit_task
from database.models import Payment
from database.repositories.payments_repo import (
    create_payment,
    get_payment_by_id,
    get_payment_by_id_for_update,
    get_user_payments,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.platega_client import PlategaClient
from services.profile_deletion_service import ProfileDeletionService
from services.referral_service import ReferralService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_alerted_paid_after_cancel: set[int] = set()
_notified_paid_after_cancel: set[int] = set()
_alerted_manual_review: set[int] = set()

_redis_client: aioredis.Redis | None = None

MANUAL_REVIEW_REASONS = {
    "banned_or_deleted": "Пользователь заблокирован или удалён",
    "inactive_tariff": "Тариф неактивен",
    "amount_mismatch": "Сумма платежа не совпадает",
    "amount_missing": "Не удалось получить сумму платежа",
    "payload_mismatch": "Несовпадение идентификатора платежа",
    "missing_tariff_or_user": "Не найден тариф или пользователь",
    "device_limit_exceeded": "Превышен лимит устройств",
    "stars_not_confirmed": "Платёж не подтверждён",
    "status_failed": "Платёж находился в статусе failed",
    "cancel_after_completed": "Отмена после успешной оплаты",
}

MANUAL_GRANT_ALLOWED_STATUSES = {
    "pending",
    "cancelled",
    "failed",
    "requires_manual_review",
}


async def _get_redis() -> aioredis.Redis:
    global _redis_client

    if _redis_client is None:
        settings = get_settings()

        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )

    return _redis_client


def _to_decimal(value) -> Decimal | None:
    """
    Безопасно конвертирует значение в Decimal.

    Использовать для финансовых данных.
    Никогда не использовать float-сравнения для денег.
    """
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _expected_payment_amount(payment: Payment) -> Decimal | None:
    """
    Возвращает ожидаемую сумму платежа по тарифу.

    Для stars: tariff.price_stars.
    Для рублей: tariff.price_rub.
    """
    if not payment.tariff:
        return None

    if payment.currency == "stars":
        return Decimal(str(payment.tariff.price_stars))

    return Decimal(str(payment.tariff.price_rub))


def _build_payment_snapshot(payment: Payment) -> dict:
    """
    Создаёт безопасный snapshot платежа для отправки алертов
    после commit.

    Важно:
    - snapshot не содержит SQLAlchemy-объекты;
    - его можно использовать в post-commit задачах;
    - личные данные минимизированы.
    """
    user = payment.user
    tariff = payment.tariff

    tariff_name = "—"

    if tariff:
        tariff_name = (
            f"{tariff.duration_days} дн. / "
            f"{tariff.device_limit} устр."
        )

    return {
        "payment_id": payment.id,
        "user_telegram_id": user.telegram_id if user else None,
        "username": (
            f"@{user.username}"
            if user and user.username
            else "—"
        ),
        "amount": str(payment.amount),
        "currency": payment.currency,
        "tariff_name": tariff_name,
        "payment_method": payment.payment_method or "—",
        "external_id": payment.external_id or "—",
    }


async def _send_alert_to_admins(
    message: str,
    keyboard=None,
) -> None:
    """
    Отправляет алерт админам.

    Используется только в post-commit задачах, чтобы не отправлять
    уведомления до фактического сохранения данных в БД.
    """
    from services.workers.heartbeat import get_bot_ref

    bot = get_bot_ref()

    if bot is None:
        logger.error(
            "Admin alert SKIPPED: bot_ref is None. "
            "Message: %s",
            message[:200],
        )
        return

    settings = get_settings()
    admin_ids = settings.ADMIN_IDS

    if not admin_ids:
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                message,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(
                "Failed to send admin alert to %s: %s",
                admin_id,
                e,
            )


async def _send_manual_review_alert_now(
    snapshot: dict,
    reason: str,
    source: str,
) -> None:
    """
    Отправляет алерт о платеже, который требует ручной проверки.
    """
    global _alerted_manual_review

    payment_id = snapshot.get("payment_id")

    if payment_id is None:
        return

    alert_key = payment_id

    if alert_key in _alerted_manual_review:
        return

    _alerted_manual_review.add(alert_key)

    from aiogram.utils.keyboard import InlineKeyboardBuilder

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

    await _send_alert_to_admins(message, keyboard)


async def _send_paid_after_cancel_alert_now(
    snapshot: dict,
) -> None:
    """
    Отправляет админам алерт о ситуации, когда оплата пришла
    после отмены платежа.
    """
    global _alerted_paid_after_cancel

    payment_id = snapshot.get("payment_id")

    if payment_id is None:
        return

    if payment_id in _alerted_paid_after_cancel:
        return

    _alerted_paid_after_cancel.add(payment_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder

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

    await _send_alert_to_admins(message, keyboard)


async def _notify_client_paid_after_cancel_now(
    snapshot: dict,
) -> None:
    """
    Уведомляет клиента, что оплата получена, но платёж был ранее
    отменён, поэтому доступ не выдан автоматически.
    """
    global _notified_paid_after_cancel

    payment_id = snapshot.get("payment_id")
    user_telegram_id = snapshot.get("user_telegram_id")

    if payment_id is None or user_telegram_id is None:
        return

    if payment_id in _notified_paid_after_cancel:
        return

    _notified_paid_after_cancel.add(payment_id)

    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.utils.keyboard import InlineKeyboardBuilder
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

        logger.info(
            "Paid-after-cancel notification sent to user %s "
            "for payment %s",
            user_telegram_id,
            payment_id,
        )

    except TelegramForbiddenError:
        logger.info(
            "Paid-after-cancel notification: user %s blocked the bot",
            user_telegram_id,
        )

        try:
            from database.connection import session_scope
            from database.repositories.users_repo import (
                mark_user_bot_blocked,
            )

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


async def _send_chargeback_alert_now(
    snapshot: dict,
    transaction_id: str,
) -> None:
    """
    Отправляет админам алерт о chargeback.
    """
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    payment_id = snapshot.get("payment_id")
    user_telegram_id = snapshot.get("user_telegram_id")

    builder = InlineKeyboardBuilder()

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


class PaymentService:

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        """
        Обрабатывает успешный платёж.

        Безопасная логика:
        1. Блокируем строку платежа.
        2. Проверяем статус.
        3. Проверяем пользователя.
        4. Проверяем тариф.
        5. Проверяем сумму.
        6. Только затем выдаём доступ.

        Если что-то не так, платёж переводится в
        requires_manual_review, а админ получает алерт.
        """
        redis = await _get_redis()

        payment_obj = await session.get(Payment, payment_id)

        if not payment_obj:
            return False, "not_found"

        user_lock_key = f"lock:payment_bonus:{payment_obj.user_id}"

        redis_lock = redis.lock(
            user_lock_key,
            timeout=30,
            blocking_timeout=15,
        )

        try:
            acquired = await redis_lock.acquire()

            if not acquired:
                logger.warning(
                    "Payment %s: failed to acquire Redis bonus lock. "
                    "Continuing with DB row lock only.",
                    payment_id,
                )

            try:
                async with session.begin_nested():
                    payment = await get_payment_by_id_for_update(
                        session,
                        payment_id,
                    )

                    if not payment:
                        return False, "not_found"

                    if payment.status == "completed":
                        logger.info(
                            "Payment %s already completed "
                            "(idempotent)",
                            payment_id,
                        )
                        return True, "already_processed"

                    if payment.status == "cancelled":
                        snapshot = _build_payment_snapshot(payment)

                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAID_AFTER_CANCEL",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                f"user="
                                f"{snapshot.get('user_telegram_id')}, "
                                f"amount={snapshot.get('amount')} "
                                f"{snapshot.get('currency')}"
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot: (
                                _send_paid_after_cancel_alert_now(s)
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot: (
                                _notify_client_paid_after_cancel_now(s)
                            ),
                        )

                        return True, "paid_after_cancel"

                    if payment.status == "refunded":
                        logger.warning(
                            "Payment %s is refunded, cannot grant "
                            "access",
                            payment_id,
                        )
                        return False, "refunded"

                    if payment.status == "requires_manual_review":
                        logger.info(
                            "Payment %s already in manual review",
                            payment_id,
                        )
                        return False, "manual_review"

                    if payment.status == "failed":
                        payment.status = "requires_manual_review"
                        payment.manual_review_reason = "status_failed"

                        await session.flush()

                        snapshot = _build_payment_snapshot(payment)

                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAYMENT_MANUAL_REVIEW",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                "reason=status_failed, "
                                "source=handle_successful_payment"
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot: (
                                _send_manual_review_alert_now(
                                    s,
                                    "status_failed",
                                    "handle_successful_payment",
                                )
                            ),
                        )

                        return False, "manual_review"

                    user = payment.user
                    tariff = payment.tariff

                    manual_review_reason = None

                    if not user or not tariff:
                        manual_review_reason = (
                            "missing_tariff_or_user"
                        )

                    elif user.is_deleted or user.is_banned:
                        manual_review_reason = "banned_or_deleted"

                    elif not tariff.is_active:
                        manual_review_reason = "inactive_tariff"

                    else:
                        expected_amount = (
                            _expected_payment_amount(payment)
                        )

                        if expected_amount is None:
                            manual_review_reason = "amount_missing"

                        elif payment.amount != expected_amount:
                            manual_review_reason = "amount_mismatch"

                            logger.error(
                                "Payment %s amount mismatch: "
                                "stored=%s, expected=%s, currency=%s",
                                payment_id,
                                payment.amount,
                                expected_amount,
                                payment.currency,
                            )

                    if manual_review_reason:
                        payment.status = "requires_manual_review"
                        payment.manual_review_reason = (
                            manual_review_reason
                        )

                        await session.flush()

                        snapshot = _build_payment_snapshot(payment)

                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAYMENT_MANUAL_REVIEW",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                f"reason={manual_review_reason}, "
                                f"source=handle_successful_payment"
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot, r=manual_review_reason: (
                                _send_manual_review_alert_now(
                                    s,
                                    r,
                                    "handle_successful_payment",
                                )
                            ),
                        )

                        return False, "manual_review"

                    # Помечаем платёж как completed.
                    payment.status = "completed"
                    payment.paid_at = now_utc()

                    await session.flush()

                    # Выдаём доступ.
                    try:
                        await SubscriptionService.extend_subscription(
                            session,
                            user.telegram_id,
                            tariff.duration_days,
                            new_device_limit=tariff.device_limit,
                            new_tariff_id=tariff.id,
                        )

                    except ValueError as e:
                        logger.error(
                            "Payment %s: subscription extend "
                            "failed: %s",
                            payment_id,
                            e,
                        )

                        payment.status = "requires_manual_review"
                        payment.manual_review_reason = (
                            "device_limit_exceeded"
                        )

                        await session.flush()

                        snapshot = _build_payment_snapshot(payment)

                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAYMENT_MANUAL_REVIEW",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                "reason=device_limit_exceeded, "
                                "source=handle_successful_payment_extend"
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot: (
                                _send_manual_review_alert_now(
                                    s,
                                    "device_limit_exceeded",
                                    "handle_successful_payment_extend",
                                )
                            ),
                        )

                        return False, "manual_review"

                    except Exception as e:
                        logger.error(
                            "Payment %s: unexpected extend error: %s",
                            payment_id,
                            e,
                            exc_info=True,
                        )

                        payment.status = "requires_manual_review"
                        payment.manual_review_reason = "status_failed"

                        await session.flush()

                        snapshot = _build_payment_snapshot(payment)

                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAYMENT_MANUAL_REVIEW",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                "reason=status_failed, "
                                "source=handle_successful_payment_extend"
                            ),
                        )

                        queue_post_commit_task(
                            session,
                            lambda s=snapshot: (
                                _send_manual_review_alert_now(
                                    s,
                                    "status_failed",
                                    "handle_successful_payment_extend",
                                )
                            ),
                        )

                        return False, "manual_review"

                    # Реферальные бонусы.
                    payments = await get_user_payments(
                        session,
                        user.id,
                    )

                    successful_payments = [
                        p
                        for p in payments
                        if p.status == "completed"
                    ]

                    is_first_payment = len(successful_payments) == 1

                    if user.referred_by:
                        try:
                            await ReferralService.process_bonus(
                                session,
                                user.telegram_id,
                                user.referred_by,
                                is_first_payment=is_first_payment,
                                duration_days=tariff.duration_days,
                            )

                        except Exception as e:
                            logger.warning(
                                "Referral bonus failed for payment "
                                "%s: %s",
                                payment_id,
                                e,
                            )

                    user.last_payment_at = now_utc()

                    invalidate_user_cache(user.telegram_id)

                    try:
                        await AuditService.log_action(
                            session,
                            admin_id=0,
                            action="PAYMENT_SUCCESS",
                            target_type="Payment",
                            target_id=payment_id,
                            details=(
                                f"user={user.telegram_id}, "
                                f"amount={payment.amount} "
                                f"{payment.currency}"
                            ),
                        )

                    except Exception as e:
                        logger.error(
                            "Failed to log payment success to "
                            "audit: %s",
                            e,
                        )

                    logger.info(
                        "Payment %s processed successfully for "
                        "user %s",
                        payment_id,
                        user.telegram_id,
                    )

                    return True, "success"

            except Exception as e:
                logger.error(
                    "Failed to process payment %s: %s",
                    payment_id,
                    e,
                    exc_info=True,
                )

                return False, "error"

        finally:
            try:
                await redis_lock.release()
            except Exception:
                pass

    @staticmethod
    async def force_grant_payment(
        session: AsyncSession,
        payment_id: int,
        admin_id: int,
    ) -> tuple:
        """
        Ручная выдача платежа админом.

        Разрешено только для безопасных статусов:
        - pending
        - cancelled
        - failed
        - requires_manual_review

        Запрещено для:
        - completed
        - refunded

        Также запрещено выдавать доступ заблокированному
        пользователю.
        """
        allowed_statuses = {
            "pending",
            "cancelled",
            "failed",
            "requires_manual_review",
        }

        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )

                if not payment:
                    return False, "Платёж не найден"

                if payment.status == "completed":
                    return False, "Платёж уже выдан"

                if payment.status == "refunded":
                    return False, (
                        "Платёж возвращён, выдача запрещена"
                    )

                if payment.status not in allowed_statuses:
                    return False, "Недопустимый статус платежа"

                user = payment.user
                tariff = payment.tariff

                if not user:
                    return False, "Пользователь не найден"

                if user.is_deleted:
                    return False, "Пользователь удалён"

                if user.is_banned:
                    return False, "Пользователь заблокирован"

                if not tariff:
                    return False, "Тариф не найден"

                payment.status = "completed"

                if not payment.paid_at:
                    payment.paid_at = now_utc()

                await session.flush()

                try:
                    await SubscriptionService.extend_subscription(
                        session,
                        user.telegram_id,
                        tariff.duration_days,
                        new_device_limit=tariff.device_limit,
                        new_tariff_id=tariff.id,
                    )

                except ValueError as e:
                    logger.error(
                        "force_grant: extend failed for payment "
                        "%s: %s",
                        payment_id,
                        e,
                    )

                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = (
                        "device_limit_exceeded"
                    )

                    await session.flush()

                    return False, "Превышен лимит устройств"

                except Exception as e:
                    logger.error(
                        "force_grant: unexpected extend error "
                        "for payment %s: %s",
                        payment_id,
                        e,
                        exc_info=True,
                    )

                    payment.status = "requires_manual_review"
                    payment.manual_review_reason = "status_failed"

                    await session.flush()

                    return False, f"Ошибка продления: {e}"

                payments = await get_user_payments(
                    session,
                    user.id,
                )

                successful_payments = [
                    p
                    for p in payments
                    if p.status == "completed"
                ]

                is_first_payment = len(successful_payments) == 1

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=tariff.duration_days,
                        )

                    except Exception as e:
                        logger.warning(
                            "Referral bonus failed for manual "
                            "grant %s: %s",
                            payment_id,
                            e,
                        )

                user.last_payment_at = now_utc()

                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=admin_id,
                        action="MANUAL_GRANT",
                        target_type="Payment",
                        target_id=payment_id,
                        details=(
                            f"Admin {admin_id} manually granted "
                            f"payment {payment_id} for user "
                            f"{user.telegram_id}"
                        ),
                    )

                except Exception as e:
                    logger.error(
                        "force_grant: audit failed: %s",
                        e,
                    )

                return True, "ok"

        except Exception as e:
            logger.error(
                "force_grant_payment failed: %s",
                e,
                exc_info=True,
            )

            return False, f"Ошибка БД: {e}"

    @staticmethod
    async def create_platega_payment(
        session: AsyncSession,
        user_id: int,
        tariff_id: int,
        amount: float,
        telegram_id: int,
        bot_username: str,
    ) -> tuple:
        """
        Создаёт платёж через платёжную систему.

        Важно:
        - сумма в БД хранится как Decimal;
        - описание платежа не содержит личных ID;
        - payload содержит только ID платежа.
        """
        settings = get_settings()

        decimal_amount = _to_decimal(amount)

        if decimal_amount is None:
            logger.error(
                "create_platega_payment: invalid amount %s",
                amount,
            )
            return None, None

        payment = await create_payment(
            session=session,
            user_id=user_id,
            tariff_id=tariff_id,
            amount=decimal_amount,
            currency="RUB",
        )

        description = f"Payment #{payment.id}"

        clean_username = bot_username.lstrip("@")

        return_url = settings.PLATEGA_RETURN_URL.format(
            bot_username=clean_username,
        )

        failed_url = settings.PLATEGA_FAILED_URL.format(
            bot_username=clean_username,
        )

        payload = f"payment_{payment.id}"

        client = PlategaClient()

        transaction = await client.create_transaction(
            amount=float(decimal_amount),
            currency="RUB",
            description=description,
            return_url=return_url,
            failed_url=failed_url,
            payload=payload,
        )

        if not transaction:
            try:
                await session.delete(payment)
                await session.flush()

            except Exception as delete_error:
                logger.error(
                    "Failed to delete phantom payment %s: %s",
                    payment.id,
                    delete_error,
                )

                payment.status = "failed"

                try:
                    await session.flush()
                except Exception:
                    pass

            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_FAILED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"user={user_id}, "
                        f"amount={decimal_amount} RUB, "
                        f"payment provider create_transaction failed"
                    ),
                )

            except Exception as e:
                logger.error(
                    "Failed to log payment failure to audit: %s",
                    e,
                )

            return None, None

        payment.external_id = transaction.get("transactionId")
        payment.payment_url = transaction.get("redirect")
        payment.payment_method = transaction.get(
            "paymentMethod",
            "SBPQR",
        )

        return payment, None

    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession,
        transaction_id: str,
        status: str,
        payload: str,
        callback_amount: float | None = None,
        callback_payload: str | None = None,
    ) -> tuple:
        stmt = (
            select(Payment)
            .options(
                selectinload(Payment.user),
                selectinload(Payment.tariff),
            )
            .where(Payment.external_id == transaction_id)
        )

        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(
                "Payment provider callback: payment not found "
                "for transaction=%s",
                transaction_id,
            )
            return False, "not_found"

        logger.info(
            "Payment provider callback: payment %s status=%s",
            payment.id,
            status,
        )

        if status == "CONFIRMED":
            if payment.status == "completed":
                logger.info(
                    "Payment provider callback: payment %s already "
                    "completed, idempotent success for "
                    "transaction=%s",
                    payment.id,
                    transaction_id,
                )
                return True, "already_processed"

            if payment.status == "cancelled":
                snapshot = _build_payment_snapshot(payment)

                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAID_AFTER_CANCEL",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )

                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _send_paid_after_cancel_alert_now(s)
                    ),
                )

                queue_post_commit_task(
                    session,
                    lambda s=snapshot: (
                        _notify_client_paid_after_cancel_now(s)
                    ),
                )

                return True, "paid_after_cancel"

            # Верификация суммы.
            if callback_amount is None:
                client = PlategaClient()

                status_data = await client.check_status(
                    transaction_id,
                )

                if (
                    status_data
                    and status_data.get("amount") is not None
                ):
                    callback_amount = float(status_data["amount"])

                    logger.info(
                        "Payment provider callback: amount recovered "
                        "via API check_status: %s for transaction=%s",
                        callback_amount,
                        transaction_id,
                    )

            if callback_amount is None:
                logger.error(
                    "Payment provider callback: amount not provided "
                    "and API verification failed for transaction=%s",
                    transaction_id,
                )

                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_missing",
                    source="platega_callback",
                )

                return False, "amount_mismatch"

            callback_decimal = _to_decimal(callback_amount)

            if callback_decimal is None:
                logger.error(
                    "Payment provider callback: invalid callback "
                    "amount %s for transaction=%s",
                    callback_amount,
                    transaction_id,
                )

                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_mismatch",
                    source="platega_callback",
                )

                return False, "amount_mismatch"

            if payment.amount != callback_decimal:
                logger.error(
                    "Payment provider amount mismatch: DB=%s, "
                    "callback=%s, payment_id=%s, transaction=%s",
                    payment.amount,
                    callback_decimal,
                    payment.id,
                    transaction_id,
                )

                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "amount_mismatch",
                    source="platega_callback",
                )

                return False, "amount_mismatch"

            expected_payload = f"payment_{payment.id}"

            if (
                callback_payload is not None
                and callback_payload != expected_payload
            ):
                logger.error(
                    "Payment provider payload mismatch: "
                    "expected=%s, callback=%s, payment_id=%s",
                    expected_payload,
                    callback_payload,
                    payment.id,
                )

                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "payload_mismatch",
                    source="platega_callback",
                )

                return False, "payload_mismatch"

            success, result_code = (
                await PaymentService.handle_successful_payment(
                    session,
                    payment.id,
                )
            )

            return success, result_code

        elif status == "CANCELED":
            if payment.status == "cancelled":
                logger.info(
                    "Payment provider callback: payment %s already "
                    "cancelled",
                    payment.id,
                )
                return True, "already_processed"

            if payment.status == "completed":
                logger.error(
                    "Payment provider callback: CANCELED received "
                    "for completed payment %s",
                    payment.id,
                )

                await PaymentService._set_manual_review(
                    session,
                    payment.id,
                    "cancel_after_completed",
                    source="platega_callback",
                )

                return True, "manual_review"

            payment.status = "cancelled"

            try:
                await AuditService.log_action(
                    session,
                    admin_id=0,
                    action="PAYMENT_CANCELLED",
                    target_type="Payment",
                    target_id=payment.id,
                    details=(
                        f"Payment provider callback: "
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )

            except Exception as e:
                logger.error(
                    "Failed to log payment cancellation to "
                    "audit: %s",
                    e,
                )

            return True, "success"

        elif status == "CHARGEBACKED":
            return await PaymentService._process_chargeback(
                session,
                payment.id,
                transaction_id,
            )

        logger.warning(
            "Unknown payment provider status: %s",
            status,
        )

        return False, "error"

    @staticmethod
    async def check_platega_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> tuple:
        payment = await get_payment_by_id(
            session,
            payment_id,
        )

        if not payment or not payment.external_id:
            return False, "not_found"

        if payment.status == "completed":
            return True, "success"

        if payment.status == "cancelled":
            return False, "cancelled"

        if payment.status == "requires_manual_review":
            return False, "manual_review"

        if payment.status == "refunded":
            return False, "refunded"

        if payment.status != "pending":
            return False, "invalid_status"

        client = PlategaClient()

        status_data = await client.check_status(
            payment.external_id,
        )

        if not status_data:
            return False, "api_error"

        status = status_data.get("status")

        if status == "CONFIRMED":
            callback_amount = status_data.get("amount")

            if callback_amount is not None:
                callback_decimal = _to_decimal(callback_amount)

                if (
                    callback_decimal is None
                    or payment.amount != callback_decimal
                ):
                    await PaymentService._set_manual_review(
                        session,
                        payment.id,
                        "amount_mismatch",
                        source="check_platega_payment",
                    )

                    return False, "manual_review"

            success, result_code = (
                await PaymentService.handle_successful_payment(
                    session,
                    payment.id,
                )
            )

            return success, result_code

        elif status == "CANCELED":
            if payment.status != "cancelled":
                payment.status = "cancelled"

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CANCELLED",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            "check_platega_payment: "
                            "status=CANCELED, "
                            f"user={payment.user_id}"
                        ),
                    )

                except Exception as e:
                    logger.error(
                        "Failed to log payment cancellation "
                        "to audit: %s",
                        e,
                    )

            return False, "cancelled"

        elif status == "CHARGEBACKED":
            return await PaymentService._process_chargeback(
                session,
                payment.id,
                payment.external_id,
            )

        return False, "pending"

    @staticmethod
    async def _set_manual_review(
        session: AsyncSession,
        payment_id: int,
        reason: str,
        source: str,
    ) -> tuple:
        """
        Переводит платёж в статус requires_manual_review.

        Используется, когда платёж нельзя безопасно обработать
        автоматически.
        """
        stmt = (
            update(Payment)
            .where(
                Payment.id == payment_id,
                Payment.status.in_(
                    [
                        "pending",
                        "failed",
                        "cancelled",
                    ]
                ),
            )
            .values(
                status="requires_manual_review",
                manual_review_reason=reason,
            )
        )

        result = await session.execute(stmt)
        await session.flush()

        if result.rowcount == 0:
            current = await session.get(Payment, payment_id)

            if current and current.status == "completed":
                return True, "already_processed"

            if (
                current
                and current.status == "requires_manual_review"
            ):
                return True, "manual_review"

            return False, current.status if current else "not_found"

        payment = await get_payment_by_id(
            session,
            payment_id,
        )

        snapshot = _build_payment_snapshot(payment)

        await AuditService.log_action(
            session,
            admin_id=0,
            action="PAYMENT_MANUAL_REVIEW",
            target_type="Payment",
            target_id=payment_id,
            details=(
                f"reason={reason}, source={source}, "
                f"user={payment.user_id if payment else '—'}"
            ),
        )

        queue_post_commit_task(
            session,
            lambda s=snapshot, r=reason, src=source: (
                _send_manual_review_alert_now(s, r, src)
            ),
        )

        return True, "manual_review"

    @staticmethod
    async def _process_chargeback(
        session: AsyncSession,
        payment_id: int,
        transaction_id: str,
    ) -> tuple:
        try:
            async with session.begin_nested():
                payment = await get_payment_by_id_for_update(
                    session,
                    payment_id,
                )

                if not payment:
                    return False, "not_found"

                if payment.status == "refunded":
                    logger.info(
                        "Payment provider callback: payment %s "
                        "already refunded",
                        payment.id,
                    )
                    return True, "already_processed"

                payment.status = "refunded"
                payment.manual_review_reason = None

                await session.flush()

                user = payment.user

                if user:
                    current_time = now_utc()

                    # Отзываем доступ.
                    user.subscription_end = current_time
                    user.current_tariff_id = None
                    user.device_limit = 0

                    await session.flush()

                    # Откатываем реферальные бонусы.
                    if user.referred_by:
                        try:
                            referrer = await get_user_by_telegram_id(
                                session,
                                user.referred_by,
                            )

                            if referrer:
                                payments = await get_user_payments(
                                    session,
                                    user.id,
                                )

                                successful_payments = [
                                    p
                                    for p in payments
                                    if p.status == "completed"
                                ]

                                is_first_payment = (
                                    len(successful_payments) <= 1
                                )

                                tariff = payment.tariff

                                if (
                                    tariff
                                    and tariff.duration_days >= 30
                                ):
                                    if is_first_payment:
                                        bonus_referrer = 3
                                    else:
                                        bonus_referrer = 1

                                    if (
                                        referrer.referral_days
                                        and referrer.referral_days
                                        >= bonus_referrer
                                    ):
                                        referrer.referral_days -= (
                                            bonus_referrer
                                        )

                                    # Не вычитаем дни из вечной
                                    # подписки.
                                    if (
                                        referrer.subscription_end
                                        and referrer.subscription_end
                                        > current_time
                                        and referrer.subscription_end.year
                                        < 2100
                                    ):
                                        referrer.subscription_end = (
                                            referrer.subscription_end
                                            - timedelta(
                                                days=bonus_referrer,
                                            )
                                        )

                                    logger.info(
                                        "Chargeback: rolled back "
                                        "referral bonus for "
                                        "referrer %s",
                                        referrer.telegram_id,
                                    )

                        except Exception as e:
                            logger.error(
                                "Chargeback: failed to rollback "
                                "referral bonuses: %s",
                                e,
                                exc_info=True,
                            )

                    # Удаляем устройства пользователя.
                    try:
                        await ProfileDeletionService.delete_profiles_for_user(
                            session,
                            user.id,
                            reason="chargeback_delete",
                            background=True,
                        )

                    except Exception as e:
                        logger.error(
                            "Chargeback: failed to delete profiles "
                            "for user %s: %s",
                            user.id,
                            e,
                            exc_info=True,
                        )

                    invalidate_user_cache(user.telegram_id)

                    logger.warning(
                        "CHARGEBACK processed: user %s, payment %s. "
                        "Access revoked and devices deleted.",
                        user.telegram_id,
                        payment.id,
                    )

                snapshot = _build_payment_snapshot(payment)

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="PAYMENT_CHARGEBACK",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            f"Payment provider chargeback: "
                            f"transaction={transaction_id}, "
                            f"user={payment.user_id}"
                        ),
                    )

                except Exception as e:
                    logger.error(
                        "Failed to log chargeback to audit: %s",
                        e,
                    )

                queue_post_commit_task(
                    session,
                    lambda s=snapshot, tid=transaction_id: (
                        _send_chargeback_alert_now(s, tid)
                    ),
                )

                return True, "success"

        except Exception as e:
            logger.error(
                "Chargeback processing failed for payment %s: %s",
                payment_id,
                e,
                exc_info=True,
            )

            return False, "error"