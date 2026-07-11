import asyncio
import logging
from aiogram import Bot
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from database.connection import get_session
from database.models import Payment
from config.settings import get_settings
from bot.constants import STALE_PAYMENT_THRESHOLD

logger = logging.getLogger("BackgroundWorker")


async def stale_payments_checker_loop(bot: Bot):
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(STALE_PAYMENT_THRESHOLD)
            session = await get_session()
            try:
                threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()
                if not stale_payments:
                    continue
                msg = f"⚠️ <b>{len(stale_payments)} зависших платежей (pending > 1ч)</b>\n"
                for p in stale_payments[:10]:
                    msg += f"ID: <code>{p.id}</code> · User: <code>{p.user_id}</code> · {p.amount} {p.currency}\n"
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