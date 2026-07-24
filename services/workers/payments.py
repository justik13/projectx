import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from cachetools import TTLCache
from sqlalchemy import select

from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from config.settings import get_settings
from database.connection import session_scope
from database.models import Payment, User
from services.payment_service import PaymentService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

# ИСПРАВЛЕНО: TTLCache вместо бесконечного set.
_alerted_stale_payments: TTLCache[int, bool] = TTLCache(maxsize=50000, ttl=7200)

PAYMENTS_START_DELAY = 60.0


async def _preload_alerted_stale_payments():
    try:
        async with session_scope() as session:
            threshold = now_utc() - timedelta(hours=1)
            stmt = select(Payment.id).where(
                Payment.status == "pending",
                Payment.created_at < threshold,
            )
            result = await session.execute(stmt)
            for (payment_id,) in result.all():
                _alerted_stale_payments[payment_id] = True
            if _alerted_stale_payments:
                logger.info(
                    "Preloaded %s existing stale payment IDs to suppress duplicate alerts after restart",
                    len(_alerted_stale_payments),
                )
    except Exception as e:
        logger.warning("Failed to preload stale payment IDs: %s", e)


async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()

    try:
        await asyncio.wait_for(shutdown_event.wait(), timeout=PAYMENTS_START_DELAY)
        logger.info("Stale payments worker stopped during start delay (shutdown)")
        return
    except asyncio.TimeoutError:
        pass

    await _preload_alerted_stale_payments()

    while not shutdown_event.is_set():
        try:
            await _process_stale_payments(bot, settings)
        except asyncio.CancelledError:
            logger.info("Stale payments worker cancelled")
            break
        except Exception as e:
            logger.error("Критическая ошибка в stale_payments_checker: %s", e, exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD)
            break
        except asyncio.TimeoutError:
            continue

    logger.info("Stale payments worker stopped gracefully")


async def _process_stale_payments(bot: Bot, settings):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=1)
    yookassa_payment_ids = []

    async with session_scope() as session:
        stmt = (
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(Payment.status == "pending", Payment.created_at < threshold)
            .order_by(Payment.created_at.desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        for payment, telegram_id in result.all():
            if payment.external_id:
                yookassa_payment_ids.append(payment.id)

    for payment_id in yookassa_payment_ids:
        try:
            async with session_scope() as session:
                await PaymentService.check_yookassa_payment(session, payment_id)
        except Exception as e:
            logger.warning("Failed to check external payment %s: %s", payment_id, e)

    await _alert_new_stale_payments(bot, settings)


async def _alert_new_stale_payments(bot: Bot, settings):
    current_time = now_utc()
    threshold = current_time - timedelta(hours=1)

    async with session_scope() as session:
        fresh_stmt = (
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(Payment.status == "pending", Payment.created_at < threshold)
            .order_by(Payment.created_at.desc())
        )
        fresh_result = await session.execute(fresh_stmt)
        fresh_stale = [(payment, telegram_id) for payment, telegram_id in fresh_result.all()]

    new_stale_for_alert = [(p, tg) for p, tg in fresh_stale if p.id not in _alerted_stale_payments]

    if not new_stale_for_alert:
        return

    msg = (
        f"⚠️ <b>Новые зависшие платежи (pending > 1ч)</b>\n"
        f"{'─' * 20}\n"
        f"Количество: <b>{len(new_stale_for_alert)}</b>\n"
    )
    for payment, telegram_id in new_stale_for_alert[:10]:
        method = payment.payment_method or "—"
        msg += f"ID: <code>{payment.id}</code> · User: <code>{telegram_id}</code> · {payment.amount} {payment.currency} · {method}\n"
    if len(new_stale_for_alert) > 10:
        msg += f"\n<i>... и ещё {len(new_stale_for_alert) - 10}</i>"

    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.error("Stale alert failed to %s: %s", admin_id, e)

    for payment, _ in new_stale_for_alert:
        _alerted_stale_payments[payment.id] = True