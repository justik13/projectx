import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_
from database.connection import get_session, session_scope
from database.repositories.tariffs_repo import get_active_tariffs, get_tariff_by_id
from database.repositories.users_repo import mark_user_bot_blocked
from database.models import User
from bot.constants import NOTIFICATION_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL

logger = logging.getLogger("BackgroundWorker")


async def subscription_notifications_loop(bot: Bot):
    """
    Фоновый воркер уведомлений о скором истечении подписки.
    🔥 ИСПРАВЛЕНО: Надежная обработка ошибок с автоматическим перезапуском.
    """
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
                
                for user in users:
                    time_left = user.subscription_end - now
                    msg = None
                    
                    if time_left <= timedelta(hours=2) and not user.notified_2h:
                        msg = (
                            "🔴 <b>Ваш доступ отключится через 2 часа!</b>\n"
                            "Не оставайтесь без ProjectX.\n"
                            "Нажмите кнопку ниже, чтобы продлить подписку в один клик."
                        )
                        user.notified_2h = True
                    elif time_left <= timedelta(days=1) and not user.notified_1d:
                        msg = (
                            "🟡 <b>Ваш доступ отключится через 1 день.</b>\n"
                            "Рекомендуем продлить подписку заранее, чтобы не потерять связь.\n"
                            "Нажмите кнопку ниже для быстрого продления."
                        )
                        user.notified_1d = True
                    elif time_left <= timedelta(days=3) and not user.notified_3d:
                        msg = (
                            "🟢 <b>Ваш доступ отключится через 3 дня.</b>\n"
                            "Успейте продлить подписку и продолжайте пользоваться сервисом без перебоев.\n"
                            "Нажмите кнопку ниже для оплаты."
                        )
                        user.notified_3d = True
                    
                    if msg:
                        tariff_id = user.current_tariff_id
                        try:
                            tariff = await get_tariff_by_id(session, user.current_tariff_id) if user.current_tariff_id else None
                            device_limit = getattr(tariff, 'device_limit', 2) if tariff else None
                            
                            kb = InlineKeyboardBuilder()
                            kb.button(text="💳 Продлить доступ", callback_data="menu_subscription")
                            kb.button(text="✅ Прочитано (убрать)", callback_data="dismiss_notification")
                            kb.adjust(1)
                            
                            if not tariff_id:
                                kb = InlineKeyboardBuilder()
                                kb.button(text="✅ Прочитано (убрать)", callback_data="dismiss_notification")
                                kb.adjust(1)
                            
                            await bot.send_message(user.telegram_id, msg, reply_markup=kb.as_markup(), parse_mode="HTML")
                            await session.commit()
                        except TelegramForbiddenError:
                            logger.info(f"User {user.telegram_id} blocked the bot")
                            try:
                                async with session_scope() as mark_session:
                                    await mark_user_bot_blocked(mark_session, user.telegram_id)
                            except Exception as e:
                                logger.error(f"Failed to mark user as bot_blocked: {e}")
                        except Exception as e:
                            logger.warning(f"Failed to send notification to {user.telegram_id}: {e}")
                            await session.rollback()
            finally:
                await session.close()
        
        except asyncio.CancelledError:
            logger.info("Notifications worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле уведомлений: {e}", exc_info=True)
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue