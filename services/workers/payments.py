import asyncio
import logging
from aiogram import Bot
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from database.connection import session_scope
from database.models import Payment
from config.settings import get_settings
from bot.constants import STALE_PAYMENT_THRESHOLD, WORKER_ERROR_SLEEP_INTERVAL
from services.payment_service import PaymentService

logger = logging.getLogger("BackgroundWorker")


async def stale_payments_checker_loop(bot: Bot, shutdown_event: asyncio.Event):
    settings = get_settings()
    
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=STALE_PAYMENT_THRESHOLD)
                break
            except asyncio.TimeoutError:
                pass

            # 🔥 ИСПРАВЛЕНО: Безопасное управление сессией
            async with session_scope() as session:
                threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()

                for payment in stale_payments[:20]:
                    if payment.external_id and payment.payment_method == "SBPQR":
                        try:
                            await PaymentService.check_platega_payment(session, payment.id)
                        except Exception as e:
                            logger.warning(f"Failed to check Platega payment {payment.id}: {e}")

                if stale_payments:
                    msg = f"⚠️ <b>{len(stale_payments)} зависших платежей (pending > 1ч)</b>\n"
                    for p in stale_payments[:10]:
                        method = p.payment_method or "Stars"
                        msg += f"ID: <code>{p.id}</code> · User: <code>{p.user_id}</code> · {p.amount} {p.currency} · {method}\n"
                    if len(stale_payments) > 10:
                        msg += f"\n<i>... и ещё {len(stale_payments) - 10}</i>"

                    for admin_id in settings.ADMIN_IDS:
                        try:
                            await bot.send_message(admin_id, msg, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Stale alert failed to {admin_id}: {e}")

        except asyncio.CancelledError:
            logger.info("Stale payments worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в stale_payments_checker: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
    
    logger.info("Stale payments worker stopped gracefully")