import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy import select, update

from bot.constants import (
    STALE_PAYMENT_THRESHOLD,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from services.audit_service import AuditService
from services.payment_service import PaymentService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

_alerted_stale_payments: set[int] = set()

# Короткая стартовая задержка вместо длительного ожидания.
PAYMENTS_START_DELAY = 60.0


async def _preload_alerted_stale_payments():
    """
    ИСПРАВЛЕНО (БАГ 10):

    При старте worker'а загружаем ID всех pending платежей
    старше 1 часа в _alerted_stale_payments.

    Это предотвращает повторные алерты админам после каждого
    рестарта бота. Без этого все stale-платежи, которые были
    до рестарта, вызвали бы новые алерты через 60 секунд
    после старта.

    Новые stale-платежи (созданные после старта worker'а)
    по-прежнему вызывают алерты как обычно.
    """
    try:
        async with session_scope() as session:
            threshold = now_utc() - timedelta(hours=1)
            stmt = (
                select(Payment.id)
                .where(
                    Payment.status == "pending",
                    Payment.created_at < threshold,
                )
            )
            result = await session.execute(stmt)
            for (payment_id,) in result.all():
                _alerted_stale_payments.add(payment_id)

            if _alerted_stale_payments:
                logger.info(
                    "Preloaded %s existing stale payment IDs "
                    "to suppress duplicate alerts after restart",
                    len(_alerted_stale_payments),
                )
    except Exception as e:
        logger.warning(
            "Failed to preload stale payment IDs: %s",
            e,
        )


async def stale_payments_checker_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
):
    settings = get_settings()

    try:
        await asyncio.wait_for(
            shutdown_event.wait(),
            timeout=PAYMENTS_START_DELAY,
        )
        logger.info(
            "Stale payments worker stopped during start delay "
            "(shutdown)"
        )
        return
    except asyncio.TimeoutError:
        pass

    #
    # ИСПРАВЛЕНО (БАГ 10):
    #
    # Загружаем существующие stale-платежи в память,
    # чтобы не отправлять повторные алерты после рестарта.
    #
    await _preload_alerted_stale_payments()

    while not shutdown_event.is_set():
        try:
            await _process_stale_payments(bot, settings)
        except asyncio.CancelledError:
            logger.info("Stale payments worker cancelled")
            break
        except Exception as e:
            logger.error(
                "Критическая ошибка в stale_payments_checker: %s",
                e,
                exc_info=True,
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue

        try:
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=STALE_PAYMENT_THRESHOLD,
            )
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Stale payments worker stopped gracefully")


async def _process_stale_payments(bot: Bot, settings):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=1)
    sbp_payment_ids = []

    async with session_scope() as session:
        stmt = (
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(
                Payment.status == "pending",
                Payment.created_at < threshold,
            )
            .order_by(Payment.created_at.desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        for payment, telegram_id in result.all():
            # ВАЖНО:
            #
            # Раньше проверка была:
            #   payment.payment_method == "SBPQR"
            #
            # Но Platega может вернуть paymentMethod как int,
            # например 2. Тогда зависший платёж не проверялся бы.
            #
            # Поэтому внешние RUB-платежи определяем по:
            #   external_id + currency == "RUB"
            if payment.external_id and payment.currency == "RUB":
                sbp_payment_ids.append(payment.id)

    # Проверяем SBP/RUB-платежи через платёжную систему.
    for payment_id in sbp_payment_ids:
        try:
            async with session_scope() as session:
                await PaymentService.check_platega_payment(
                    session,
                    payment_id,
                )
        except Exception as e:
            logger.warning(
                "Failed to check external payment %s: %s",
                payment_id,
                e,
            )

    # Алерт по новым зависшим pending-платежам.
    await _alert_new_stale_payments(bot, settings)


async def _alert_new_stale_payments(bot: Bot, settings):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=1)
    new_stale_for_alert = []

    async with session_scope() as session:
        fresh_stmt = (
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(
                Payment.status == "pending",
                Payment.created_at < threshold,
            )
            .order_by(Payment.created_at.desc())
        )
        fresh_result = await session.execute(fresh_stmt)
        fresh_stale = [
            (payment, telegram_id)
            for payment, telegram_id in fresh_result.all()
        ]

    current_stale_ids = {p.id for p, _ in fresh_stale}
    _alerted_stale_payments.intersection_update(
        current_stale_ids,
    )

    new_stale_for_alert = [
        (p, tg)
        for p, tg in fresh_stale
        if p.id not in _alerted_stale_payments
    ]

    if not new_stale_for_alert:
        return

    msg = (
        f"⚠️ <b>Новые зависшие платежи (pending > 1ч)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Количество: <b>{len(new_stale_for_alert)}</b>\n"
    )
    for payment, telegram_id in new_stale_for_alert[:10]:
        method = payment.payment_method or "—"
        msg += (
            f"ID: <code>{payment.id}</code> · "
            f"User: <code>{telegram_id}</code> · "
            f"{payment.amount} {payment.currency} · {method}\n"
        )
    if len(new_stale_for_alert) > 10:
        msg += f"\n<i>... и ещё {len(new_stale_for_alert) - 10}</i>"

    admin_ids = settings.ADMIN_IDS
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                msg,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(
                "Stale alert failed to %s: %s",
                admin_id,
                e,
            )

    for payment, _ in new_stale_for_alert:
        _alerted_stale_payments.add(payment.id)