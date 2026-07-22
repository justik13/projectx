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

# Через сколько часов Stars-платёж считается подозрительным
# и переводится в ручную проверку.
STARS_MANUAL_REVIEW_HOURS = 24


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
    stars_payments_for_review = []

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

            elif payment.currency == "stars":
                stars_threshold = current_time - timedelta(
                    hours=STARS_MANUAL_REVIEW_HOURS,
                )

                if payment.created_at < stars_threshold:
                    stars_payments_for_review.append(
                        (payment, telegram_id)
                    )

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

    # Stars-платежи, которые не подтвердились за 24 часа,
    # переводятся в requires_manual_review.
    if stars_payments_for_review:
        await _mark_stars_payments_manual_review(
            stars_payments_for_review,
        )

        await _send_stars_manual_review_alert(
            bot,
            settings,
            stars_payments_for_review,
        )

    # Алерт по новым зависшим pending-платежам.
    await _alert_new_stale_payments(bot, settings)


async def _mark_stars_payments_manual_review(
    stars_payments: list[tuple[Payment, int]],
):
    async with session_scope() as session:
        for payment, telegram_id in stars_payments:
            try:
                await session.execute(
                    update(Payment)
                    .where(
                        Payment.id == payment.id,
                        Payment.status == "pending",
                    )
                    .values(
                        status="requires_manual_review",
                        manual_review_reason="stars_not_confirmed",
                    )
                )

                await session.flush()

                try:
                    await AuditService.log_action(
                        session,
                        admin_id=0,
                        action="STARS_PAYMENT_MANUAL_REVIEW",
                        target_type="Payment",
                        target_id=payment.id,
                        details=(
                            "Stars payment was not confirmed "
                            f"after {STARS_MANUAL_REVIEW_HOURS}h"
                        ),
                    )
                except Exception:
                    pass

                logger.info(
                    "Stars payment %s moved to manual review "
                    "(not confirmed after %s hours), user=%s",
                    payment.id,
                    STARS_MANUAL_REVIEW_HOURS,
                    telegram_id,
                )

            except Exception as e:
                logger.warning(
                    "Failed to move Stars payment %s to manual "
                    "review: %s",
                    payment.id,
                    e,
                )


async def _send_stars_manual_review_alert(
    bot: Bot,
    settings,
    stars_payments: list[tuple[Payment, int]],
):
    if not stars_payments:
        return

    admin_ids = settings.ADMIN_IDS

    if not admin_ids:
        return

    msg = (
        f"⚠️ <b>Stars-платежи требуют проверки</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Платежи не подтвердились автоматически за "
        f"{STARS_MANUAL_REVIEW_HOURS} ч.\n"
    )

    for payment, telegram_id in stars_payments[:10]:
        msg += (
            f"ID: <code>{payment.id}</code> · "
            f"User: <code>{telegram_id}</code> · "
            f"{payment.amount} {payment.currency}\n"
        )

    if len(stars_payments) > 10:
        msg += f"\n<i>... и ещё {len(stars_payments) - 10}</i>"

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id,
                msg,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(
                "Stars manual review alert failed to %s: %s",
                admin_id,
                e,
            )


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