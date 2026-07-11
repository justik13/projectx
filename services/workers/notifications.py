import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_
from database.connection import get_session
from database.repositories.tariffs_repo import get_active_tariffs
from database.repositories.users_repo import mark_user_bot_blocked
from database.models import User
from bot.keyboards import get_payment_method_keyboard
from bot.constants import NOTIFICATION_INTERVAL

logger = logging.getLogger("BackgroundWorker")


async def subscription_notifications_loop(bot: Bot):
    while True:
        try:
            await asyncio.sleep(NOTIFICATION_INTERVAL)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            session = await get_session()
            try:
                stmt = select(User).where(
                    User.subscription_end > now,
                    User.subscription_end <= now + timedelta(days=3),
                    User.is_banned == False,
                    User.is_bot_blocked == False,
                    or_(
                        User.notified_3d == False,
                        User.notified_1d == False,
                        User.notified_2h == False
                    )
                )
                users = (await session.execute(stmt)).scalars().all()
                if not users:
                    continue
                tariffs = await get_active_tariffs(session)
                tariff_id = tariffs[0].id if tariffs else None

                for user in users:
                    time_left = user.subscription_end - now
                    msg = None
                    if time_left <= timedelta(hours=2) and not user.notified_2h:
                        msg = ("🔴 <b>Ваш доступ отключится через 2 часа!</b>\n"
                               "Не оставайтесь без защищённой сети.\n"
                               "Нажмите кнопку ниже, чтобы продлить подписку в один клик.")
                        user.notified_2h = True
                    elif time_left <= timedelta(days=1) and not user.notified_1d:
                        msg = ("🟡 <b>Ваш доступ отключится через 1 день.</b>\n"
                               "Рекомендуем продлить подписку заранее, чтобы не потерять связь.\n"
                               "Нажмите кнопку ниже для быстрого продления.")
                        user.notified_1d = True
                    elif time_left <= timedelta(days=3) and not user.notified_3d:
                        msg = ("🟢 <b>Ваш доступ отключится через 3 дня.</b>\n"
                               "Успейте продлить подписку и продолжайте пользоваться сервисом без перебоев.\n"
                               "Нажмите кнопку ниже для оплаты.")
                        user.notified_3d = True

                    if msg:
                        try:
                            kb = get_payment_method_keyboard(tariff_id) if tariff_id else None
                            await bot.send_message(user.telegram_id, msg, reply_markup=kb, parse_mode="HTML")
                            await session.commit()
                        except TelegramForbiddenError:
                            logger.info(f"User {user.telegram_id} blocked the bot")
                            try:
                                await mark_user_bot_blocked(session, user.telegram_id)
                            except Exception as e:
                                logger.error(f"Failed to mark user as bot_blocked: {e}")
                            await session.rollback()
                        except Exception as e:
                            logger.warning(f"Failed to send notification to {user.telegram_id}: {e}")
                            await session.rollback()
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле уведомлений: {e}", exc_info=True)
            await asyncio.sleep(60)