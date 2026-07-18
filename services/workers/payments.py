import asyncio
import logging
from aiogram import Bot
from datetime import timedelta
from sqlalchemy import select
from database.connection import session_scope
from database.models import Payment
from config.settings import get_settings
from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from services.payment_service import PaymentService
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

# 🔥 ИСПРАВЛЕНО HIGH: Кэш для дедупликации алертов Stale Payments
# Хранит ID платежей, по которым уже отправлен алерт админам.
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

            async with session_scope() as session:
                # 🔥 ИЗМЕНЕНО: now_utc() вместо datetime.now(timezone.utc).replace(tzinfo=None)
                current_time = now_utc()
                threshold = current_time - timedelta(hours=1)

                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()

                # Проверяем статус в Platega для всех зависших (включая старые)
                for payment in stale_payments[:20]:
                    if payment.external_id and payment.payment_method == "SBPQR":
                        try:
                            await PaymentService.check_platega_payment(session, payment.id)
                        except Exception as e:
                            logger.warning(f"Failed to check Platega payment {payment.id}: {e}")

                # 🔥 ИСПРАВЛЕНО HIGH: Дедупликация алертов
                current_stale_ids = {p.id for p in stale_payments}

                # Удаляем из кэша те платежи, которые больше не висят (оплачены/отменены)
                _alerted_stale_payments.intersection_update(current_stale_ids)

                # Фильтруем только НОВЫЕ зависшие платежи
                new_stale = [p for p in stale_payments if p.id not in _alerted_stale_payments]

                if new_stale:
                    msg = f"⚠️ <b>{len(new_stale)} НОВЫХ зависших платежей (pending > 1ч)</b>\n"
                    for p in new_stale[:10]:
                        method = p.payment_method or "Stars"
                        msg += f"ID: <code>{p.id}</code> · User: <code>{p.user_id}</code> · {p.amount} {p.currency} · {method}\n"
                    if len(new_stale) > 10:
                        msg += f"\n<i>... и ещё {len(new_stale) - 10}</i>"

                    for admin_id in settings.ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, msg, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Stale alert failed to {admin_id}: {e}")

                    # Добавляем в кэш, чтобы не алертить снова в следующем цикле
                    for p in new_stale:
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