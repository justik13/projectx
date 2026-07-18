import asyncio
import logging
from aiogram import Bot
from datetime import timedelta
from sqlalchemy import select, update
from database.connection import session_scope
from database.models import Payment
from config.settings import get_settings
from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from services.payment_service import PaymentService
from services.audit_service import AuditService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")
_alerted_stale_payments: set[int] = set()
async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD)
                break
            except asyncio.TimeoutError:
                pass
            sbp_payment_ids = []
            stars_payment_ids = []

            async with session_scope() as session:
                current_time = now_utc()
                threshold = current_time - timedelta(hours=1)
                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()
                for payment in stale_payments[:20]:
                    if payment.external_id and payment.payment_method == "SBPQR":
                        sbp_payment_ids.append(payment.id)
                    elif payment.currency == "stars":
                        stars_threshold = current_time - timedelta(hours=24)
                        if payment.created_at < stars_threshold:
                            stars_payment_ids.append(payment.id)
            for payment_id in sbp_payment_ids:
                try:
                    async with session_scope() as session:
                        await PaymentService.check_platega_payment(session, payment_id)
                except Exception as e:
                    logger.warning(f"Failed to check Platega payment {payment_id}: {e}")
            if stars_payment_ids:
                async with session_scope() as session:
                    for payment_id in stars_payment_ids:
                        try:
                            await session.execute(
                                update(Payment)
                                .where(Payment.id == payment_id, Payment.status == 'pending')
                                .values(status='failed')
                            )
                            await session.flush()
                            try:
                                await AuditService.log_action(
                                    session, admin_id=0, action="STARS_PAYMENT_EXPIRED",
                                    target_type="Payment", target_id=payment_id,
                                    details=f"Stars payment expired after 24h (no payment received)"
                                )
                            except Exception:
                                pass
                            logger.info(
                                f"Stars payment {payment_id} marked as failed "
                                f"(expired after 24h)"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to expire Stars payment {payment_id}: {e}")
            new_stale_for_alert = []
            async with session_scope() as session:
                current_time = now_utc()
                threshold = current_time - timedelta(hours=1)
                fresh_stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                )
                fresh_result = await session.execute(fresh_stmt)
                fresh_stale = fresh_result.scalars().all()

                current_stale_ids = {p.id for p in fresh_stale}
                _alerted_stale_payments.intersection_update(current_stale_ids)

                new_stale_for_alert = [
                    p for p in fresh_stale
                    if p.id not in _alerted_stale_payments
                ]

            if new_stale_for_alert:
                msg = f"⚠️ <b>{len(new_stale_for_alert)} НОВЫХ зависших платежей (pending > 1ч)</b>\n"
                for p in new_stale_for_alert[:10]:
                    method = p.payment_method or "Stars"
                    msg += (
                        f"ID: <code>{p.id}</code> · "
                        f"User: <code>{p.user_id}</code> · "
                        f"{p.amount} {p.currency} · {method}\n"
                    )
                if len(new_stale_for_alert) > 10:
                    msg += f"\n<i>... и ещё {len(new_stale_for_alert) - 10}</i>"

                for admin_id in settings.ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Stale alert failed to {admin_id}: {e}")

                for p in new_stale_for_alert:
                    _alerted_stale_payments.add(p.id)

        except asyncio.CancelledError:
            logger.info("Stale payments worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в stale_payments_checker: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

    logger.info("Stale payments worker stopped gracefully")