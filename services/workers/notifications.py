import asyncio
import logging
import time
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import timedelta
from sqlalchemy import select, or_
from database.connection import session_scope
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import mark_user_bot_blocked
from database.models import User
from bot.constants import NOTIFICATION_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

MAX_RETRY_COUNT = 4
BACKOFF_BASE_INTERVAL = NOTIFICATION_INTERVAL


# 🔥 НОВОЕ: Token Bucket Rate Limiter для уведомлений
# Telegram позволяет ~30 сообщений/сек в разные чаты.
# Используем 25 msg/s с запасом.
class NotificationRateLimiter:
    def __init__(self, rate: float = 25.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                return await self.acquire()
            self.tokens -= 1.0

_notification_limiter = NotificationRateLimiter(rate=25.0)


def _get_backoff_delay(retry_count: int) -> int:
    capped = min(retry_count, MAX_RETRY_COUNT)
    return BACKOFF_BASE_INTERVAL * (2 ** capped)


async def subscription_notifications_loop(bot: Bot, shutdown_event: asyncio.Event):
    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=NOTIFICATION_INTERVAL
                )
                break
            except asyncio.TimeoutError:
                pass

            # 🔥 ИЗМЕНЕНО: now_utc() вместо datetime.now(timezone.utc).replace(tzinfo=None)
            current_time = now_utc()
            blocked_user_ids = []

            async with session_scope() as session:
                stmt = select(User).where(
                    User.subscription_end > current_time,
                    User.subscription_end <= current_time + timedelta(days=3),
                    User.is_banned == False,
                    User.is_bot_blocked == False,
                    User.is_deleted == False,
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
                    time_left = user.subscription_end - current_time
                    msg = None
                    notification_type = None
                    retry_count = user.notification_retry_count or 0

                    if retry_count >= MAX_RETRY_COUNT:
                        user.notification_retry_count = 0
                        continue

                    if retry_count > 0 and user.last_notification_attempt:
                        backoff_delay = _get_backoff_delay(retry_count - 1)
                        time_since_last = (
                            current_time - user.last_notification_attempt
                        ).total_seconds()
                        if time_since_last < backoff_delay:
                            continue

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
                        try:
                            # 🔥 ИСПРАВЛЕНО: Rate limiter перед отправкой
                            await _notification_limiter.acquire()

                            tariff = await get_tariff_by_id(
                                session, user.current_tariff_id
                            ) if user.current_tariff_id else None

                            kb = InlineKeyboardBuilder()
                            if tariff:
                                kb.button(
                                    text="💳 Продлить доступ",
                                    callback_data="menu_subscription"
                                )
                            kb.button(
                                text="✅ Прочитано (убрать)",
                                callback_data="dismiss_notification"
                            )
                            kb.adjust(1)

                            await bot.send_message(
                                user.telegram_id, msg,
                                reply_markup=kb.as_markup(),
                                parse_mode="HTML"
                            )

                            user.notification_retry_count = 0
                            user.last_notification_attempt = current_time

                            if notification_type == "2h":
                                user.notified_2h = True
                            elif notification_type == "1d":
                                user.notified_1d = True
                            elif notification_type == "3d":
                                user.notified_3d = True

                        except TelegramForbiddenError:
                            logger.info(
                                f"User {user.telegram_id} blocked the bot"
                            )
                            blocked_user_ids.append(user.telegram_id)
                        except Exception as e:
                            user.notification_retry_count = retry_count + 1
                            user.last_notification_attempt = current_time
                            logger.warning(
                                f"Failed to send notification to "
                                f"{user.telegram_id}: {e}"
                            )

                if blocked_user_ids:
                    try:
                        for uid in blocked_user_ids:
                            await mark_user_bot_blocked(session, uid)
                    except Exception as e:
                        logger.error(
                            f"Failed to batch mark users as bot_blocked: {e}"
                        )

        except asyncio.CancelledError:
            logger.info("Notifications worker cancelled")
            break
        except Exception as e:
            logger.error(
                f"Критическая ошибка в цикле уведомлений: {e}", exc_info=True
            )
            if shutdown_event.is_set():
                break
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

    logger.info("Notifications worker stopped gracefully")