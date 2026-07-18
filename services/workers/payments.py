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

# 🔥 ИСПРАВЛЕНО HIGH: Кэш для дедупликации алертов Stale Payments
_alerted_stale_payments: set[int] = set()


# ═══════════════════════════════════════════════════════════
# 🔥 ИСПРАВЛЕНО P1-2: HTTP-запросы к Platega вынесены за пределы транзакции
# Было:
#   async with session_scope() as session:              # ← ОТКРЫТА ТРАНЗАКЦИЯ
#       stale_payments = ...
#       for payment in stale_payments[:20]:
#           await PaymentService.check_platega_payment(session, payment.id)
#           # ↑ ВНУТРИ: HTTP к Platega (timeout=30s!) × 20 = 10 минут!
# Стало:
#   ШАГ 1: SELECT stale payments (быстрая транзакция, ~10ms)
#   ШАГ 2: Для каждого платежа ОТДЕЛЬНАЯ session_scope() с HTTP
#   → Каждая транзакция держит соединение ~2 секунды вместо 10 минут
# ═══════════════════════════════════════════════════════════
async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD)
                break
            except asyncio.TimeoutError:
                pass

            # ═══════════════════════════════════════════════════════════
            # ШАГ 1: Загружаем stale payments (быстрый SELECT)
            # ═══════════════════════════════════════════════════════════
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

                # Разделяем по типам
                for payment in stale_payments[:20]:
                    if payment.external_id and payment.payment_method == "SBPQR":
                        sbp_payment_ids.append(payment.id)
                    elif payment.currency == "stars":
                        stars_threshold = current_time - timedelta(hours=24)
                        if payment.created_at < stars_threshold:
                            stars_payment_ids.append(payment.id)

            # ═══════════════════════════════════════════════════════════
            # ШАГ 2: Проверяем Platega платежи ОТДЕЛЬНЫМИ транзакциями
            # Каждая транзакция = 1 SELECT + 1 HTTP + 1 UPDATE = ~2 секунды
            # ═══════════════════════════════════════════════════════════
            for payment_id in sbp_payment_ids:
                try:
                    async with session_scope() as session:
                        await PaymentService.check_platega_payment(session, payment_id)
                except Exception as e:
                    logger.warning(f"Failed to check Platega payment {payment_id}: {e}")

            # ═══════════════════════════════════════════════════════════
            # ШАГ 3: Просроченные Stars-платежи (batch UPDATE)
            # ═══════════════════════════════════════════════════════════
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

            # ═══════════════════════════════════════════════════════════
            # ШАГ 4: Алерты админам о новых зависших платежах
            # ═══════════════════════════════════════════════════════════
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