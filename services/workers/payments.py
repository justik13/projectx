import asyncio
import logging
from aiogram import Bot
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from database.connection import get_session
from database.models import Payment
from config.settings import get_settings
from bot.constants import STALE_PAYMENT_THRESHOLD
from services.payment_service import PaymentService

logger = logging.getLogger("BackgroundWorker")

async def stale_payments_checker_loop(bot: Bot):
    """Проверяет зависшие платежи и уведомляет админов"""
    settings = get_settings()
    
    while True:
        try:
            await asyncio.sleep(STALE_PAYMENT_THRESHOLD)
            
            session = await get_session()
            try:
                # Проверяем старые pending платежи
                threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()
                
                # 🆕 Автоматически проверяем статус Platega платежей
                for payment in stale_payments[:20]:
                    if payment.external_id and payment.payment_method == "SBPQR":
                        try:
                            await PaymentService.check_platega_payment(session, payment.id)
                        except Exception as e:
                            logger.warning(f"Failed to check Platega payment {payment.id}: {e}")
                
                # Уведомляем админов если есть зависшие платежи
                if stale_payments:
                    msg = f"⚠️ <b>{len(stale_payments)} зависших платежей (pending > 1ч)</b>\n\n"
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
                            
            finally:
                await session.close()
                
        except Exception as e:
            logger.error(f"Ошибка в stale_payments_checker: {e}", exc_info=True)