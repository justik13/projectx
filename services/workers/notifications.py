import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, or_
from database.connection import get_session, session_scope
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import mark_user_bot_blocked
from database.models import User
from bot.constants import NOTIFICATION_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL

logger = logging.getLogger("BackgroundWorker")


async def subscription_notifications_loop(bot: Bot):
    """Фоновый воркер уведомлений о скором истечении подписки."""
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

                blocked_user_ids = []

                for user in users:
                    time_left = user.subscription_end - now
                    msg = None
                    notification_type = None

                    # Определяем тип уведомления
                    if time_left <= timedelta(hours=2) and not user.notified_2h:
                        msg = (
                            "🔴 <b>Ваш доступ отключится через 2 часа!</b>\n"
                            "Не оставайтесь без ProjectX.\n"
                            "Нажмите кнопку ниже, чтобы продлить подписку в один клик."
                        )
                        notification_type = "2h"
                    elif time_left <= timedelta(days=1) and not user.notified_1d:
                        msg = (
                            "🟡 <b>Ваш доступ отключится через 1 день.</b>\n"
                            "Рекомендуем продлить подписку заранее, чтобы не потерять связь.\n"
                            "Нажмите кнопку ниже для быстрого продления."
                        )
                        notification_type = "1d"
                    elif time_left <= timedelta(days=3) and not user.notified_3d:
                        msg = (
                            "🟢 <b>Ваш доступ отключится через 3 дня.</b>\n"
                            "Успейте продлить подписку и продолжайте пользоваться сервисом без перебоев.\n"
                            "Нажмите кнопку ниже для оплаты."
                        )
                        notification_type = "3d"

                    if msg:
                        tariff_id = user.current_tariff_id
                        try:
                            tariff = await get_tariff_by_id(session, user.current_tariff_id) if user.current_tariff_id else None

                            kb = InlineKeyboardBuilder()
                            kb.button(text="💳 Продлить доступ", callback_data="menu_subscription")
                            kb.button(text="✅ Прочитано (убрать)", callback_data="dismiss_notification")
                            kb.adjust(1)

                            if not tariff_id:
                                kb = InlineKeyboardBuilder()
                                kb.button(text="✅ Прочитано (убрать)", callback_data="dismiss_notification")
                                kb.adjust(1)

                            # Отправляем уведомление
                            await bot.send_message(user.telegram_id, msg, reply_markup=kb.as_markup(), parse_mode="HTML")

                            # 🔥 ИСПРАВЛЕНО #9: Помечаем как уведомлённого ТОЛЬКО после успешной отправки
                            if notification_type == "2h":
                                user.notified_2h = True
                            elif notification_type == "1d":
                                user.notified_1d = True
                            elif notification_type == "3d":
                                user.notified_3d = True

                        except TelegramForbiddenError:
                            # Пользователь заблокировал бота — добавляем в batch для пометки
                            logger.info(f"User {user.telegram_id} blocked the bot")
                            blocked_user_ids.append(user.telegram_id)

                        except Exception as e:
                            # 🔥 ИСПРАВЛЕНО #9: При ошибке отправки НЕ помечаем как уведомлённого
                            # user.notified_Xd остаётся False, и попытка повторится в следующем цикле
                            # 🔥 ИСПРАВЛЕНО: Убран rollback() — он откатывал изменения других пользователей
                            logger.warning(f"Failed to send notification to {user.telegram_id}: {e}")

                # 🔥 ИСПРАВЛЕНО #9: Commit только успешных изменений (один раз на всех)
                await session.commit()

                # Batch update для пользователей, заблокировавших бота
                if blocked_user_ids:
                    try:
                        async with session_scope() as mark_session:
                            for uid in blocked_user_ids:
                                await mark_user_bot_blocked(mark_session, uid)
                    except Exception as e:
                        logger.error(f"Failed to batch mark users as bot_blocked: {e}")

            finally:
                await session.close()

        except asyncio.CancelledError:
            logger.info("Notifications worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле уведомлений: {e}", exc_info=True)
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue