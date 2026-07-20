import asyncio
import logging
import time
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import or_, select

from bot.constants import (
    NOTIFICATION_INTERVAL,
    WORKER_ERROR_SLEEP_INTERVAL,
)
from database.connection import session_scope
from database.models import Tariff, User
from database.repositories.users_repo import mark_user_bot_blocked
from utils.datetime_helpers import now_utc

logger = logging.getLogger("BackgroundWorker")

MAX_RETRY_COUNT = 4
BACKOFF_BASE_INTERVAL = NOTIFICATION_INTERVAL
NOTIFICATION_BATCH_SIZE = 20

# Короткая стартовая задержка вместо 30 минут.
NOTIFICATION_START_DELAY = 60.0

# Grace-период после истечения подписки.
GRACE_PERIOD_HOURS = 48


class NotificationRateLimiter:
    def __init__(self, rate: float = 25.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill

                self.tokens = min(
                    self.rate,
                    self.tokens + elapsed * self.rate,
                )

                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

            wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


_notification_limiter = NotificationRateLimiter(rate=25.0)


def _get_backoff_delay(retry_count: int) -> int:
    capped = min(retry_count, MAX_RETRY_COUNT)
    return BACKOFF_BASE_INTERVAL * (2 ** capped)


def _format_countdown(delta: timedelta) -> str:
    if delta.total_seconds() <= 0:
        return "в ближайшее время"

    days = delta.days
    hours = delta.seconds // 3600

    if days > 0:
        return f"{days} дн. {hours} ч."

    minutes = (delta.seconds % 3600) // 60

    return f"{hours} ч. {minutes} мин."


async def subscription_notifications_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
):
    try:
        await asyncio.wait_for(
            shutdown_event.wait(),
            timeout=NOTIFICATION_START_DELAY,
        )

        logger.info(
            "Notifications worker stopped during start delay (shutdown)"
        )

        return

    except asyncio.TimeoutError:
        pass

    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=NOTIFICATION_INTERVAL,
                )
                break

            except asyncio.TimeoutError:
                pass

            current_time = now_utc()

            await _send_pre_expiry_notifications(bot, current_time)
            await _send_post_expiry_notifications(bot, current_time)

        except asyncio.CancelledError:
            logger.info("Notifications worker cancelled")
            break

        except Exception as e:
            logger.error(
                "Критическая ошибка в цикле уведомлений: %s",
                e,
                exc_info=True,
            )

            if shutdown_event.is_set():
                break

            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)

    logger.info("Notifications worker stopped gracefully")


async def _send_pre_expiry_notifications(
    bot: Bot,
    current_time,
):
    """
    Уведомления до истечения подписки:
    - за 3 дня;
    - за 1 день;
    - за 2 часа.
    """
    users = []
    tariff_cache: dict[int, Tariff] = {}

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
                User.notified_2h == False,
            ),
        )

        result = await session.execute(stmt)
        users = list(result.scalars().all())

        tariff_result = await session.execute(select(Tariff))
        tariff_cache = {
            tariff.id: tariff
            for tariff in tariff_result.scalars().all()
        }

    if not users:
        return

    logger.info(
        "Pre-expiry notifications: found %s users, %s tariffs cached",
        len(users),
        len(tariff_cache),
    )

    all_blocked_user_ids = []

    for i in range(0, len(users), NOTIFICATION_BATCH_SIZE):
        batch = users[i : i + NOTIFICATION_BATCH_SIZE]
        blocked_user_ids = []

        async with session_scope() as session:
            for user in batch:
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

                if (
                    time_left <= timedelta(hours=2)
                    and not user.notified_2h
                ):
                    msg = (
                        "🔴 <b>Ваш доступ отключится через 2 часа!</b>\n"
                        "Не оставайтесь без ProjectX.\n"
                        "Нажмите кнопку ниже, чтобы продлить подписку."
                    )
                    notification_type = "2h"

                elif (
                    time_left <= timedelta(days=1)
                    and not user.notified_1d
                ):
                    msg = (
                        "🟡 <b>Ваш доступ отключится через 1 день.</b>\n"
                        "Рекомендуем продлить подписку заранее.\n"
                        "Нажмите кнопку ниже для быстрого продления."
                    )
                    notification_type = "1d"

                elif (
                    time_left <= timedelta(days=3)
                    and not user.notified_3d
                ):
                    msg = (
                        "🟢 <b>Ваш доступ отключится через 3 дня.</b>\n"
                        "Успейте продлить подписку и продолжайте "
                        "пользоваться сервисом без перебоев.\n"
                        "Нажмите кнопку ниже для оплаты."
                    )
                    notification_type = "3d"

                if not msg:
                    continue

                try:
                    await _notification_limiter.acquire()

                    tariff = (
                        tariff_cache.get(user.current_tariff_id)
                        if user.current_tariff_id
                        else None
                    )

                    builder = InlineKeyboardBuilder()

                    if tariff:
                        builder.button(
                            text="💳 Продлить доступ",
                            callback_data="menu_subscription",
                        )
                    else:
                        builder.button(
                            text="💳 Купить доступ",
                            callback_data="menu_buy",
                        )

                    builder.button(
                        text="✅ Прочитано (убрать)",
                        callback_data="dismiss_notification",
                    )

                    builder.adjust(1)

                    await bot.send_message(
                        user.telegram_id,
                        msg,
                        reply_markup=builder.as_markup(),
                        parse_mode="HTML",
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
                        "User %s blocked the bot",
                        user.telegram_id,
                    )
                    blocked_user_ids.append(user.telegram_id)

                except Exception as e:
                    user.notification_retry_count = retry_count + 1
                    user.last_notification_attempt = current_time

                    logger.warning(
                        "Failed to send pre-expiry notification "
                        "to %s: %s",
                        user.telegram_id,
                        e,
                    )

        if blocked_user_ids:
            try:
                async with session_scope() as session:
                    for uid in blocked_user_ids:
                        await mark_user_bot_blocked(session, uid)

            except Exception as e:
                logger.error(
                    "Failed to batch mark users as bot_blocked: %s",
                    e,
                )

        all_blocked_user_ids.extend(blocked_user_ids)

    if all_blocked_user_ids:
        logger.info(
            "Pre-expiry notifications: %s users blocked the bot",
            len(all_blocked_user_ids),
        )


async def _send_post_expiry_notifications(
    bot: Bot,
    current_time,
):
    """
    Уведомления после истечения подписки:

    1. Сразу после истечения:
       - подписка истекла;
       - устройства будут удалены через 48 часов.

    2. За 12 часов до удаления устройств:
       - осталось 12 часов;
       - нужно продлить доступ.
    """
    grace_start = current_time - timedelta(hours=GRACE_PERIOD_HOURS)

    async with session_scope() as session:
        stmt = (
            select(User.id)
            .where(
                User.subscription_end != None,
                User.subscription_end < current_time,
                User.subscription_end > grace_start,
                User.is_banned == False,
                User.is_bot_blocked == False,
                User.is_deleted == False,
                or_(
                    User.notified_expired == False,
                    User.notified_grace_12h == False,
                ),
            )
            .order_by(User.subscription_end.asc())
            .limit(200)
        )

        result = await session.execute(stmt)
        user_ids = [row[0] for row in result.all()]

    if not user_ids:
        return

    logger.info(
        "Post-expiry notifications: found %s users",
        len(user_ids),
    )

    for i in range(0, len(user_ids), NOTIFICATION_BATCH_SIZE):
        batch_ids = user_ids[i : i + NOTIFICATION_BATCH_SIZE]

        async with session_scope() as session:
            users_stmt = select(User).where(User.id.in_(batch_ids))
            users_result = await session.execute(users_stmt)
            batch_users = list(users_result.scalars().all())

            for user in batch_users:
                if not user.subscription_end:
                    continue

                if user.subscription_end >= current_time:
                    continue

                if user.is_banned or user.is_bot_blocked or user.is_deleted:
                    continue

                deletion_time = user.subscription_end + timedelta(
                    hours=GRACE_PERIOD_HOURS,
                )

                if current_time >= deletion_time:
                    continue

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

                time_until_delete = deletion_time - current_time

                msg = None
                notification_type = None

                if (
                    not user.notified_grace_12h
                    and current_time
                    >= deletion_time - timedelta(hours=12)
                ):
                    msg = (
                        "⚠️ <b>Осталось 12 часов до удаления устройств</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Подписка истекла.\n"
                        "Если вы не продлите доступ, устройства будут "
                        "удалены.\n"
                        "Продлите доступ, чтобы сохранить их."
                    )
                    notification_type = "grace_12h"

                elif not user.notified_expired:
                    countdown = _format_countdown(time_until_delete)

                    msg = (
                        "🔴 <b>Подписка истекла</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "Ваши устройства перестали работать.\n"
                        f"Устройства будут удалены через: <b>{countdown}</b>\n"
                        "Продлите доступ, чтобы сохранить их."
                    )
                    notification_type = "expired"

                if not msg:
                    continue

                try:
                    await _notification_limiter.acquire()

                    builder = InlineKeyboardBuilder()

                    builder.button(
                        text="🚀 Купить доступ",
                        callback_data="menu_buy",
                    )

                    builder.button(
                        text="💬 Поддержка",
                        callback_data="menu_support",
                    )

                    builder.button(
                        text="✅ Прочитано (убрать)",
                        callback_data="dismiss_notification",
                    )

                    builder.adjust(1)

                    await bot.send_message(
                        user.telegram_id,
                        msg,
                        reply_markup=builder.as_markup(),
                        parse_mode="HTML",
                    )

                    user.notification_retry_count = 0
                    user.last_notification_attempt = current_time

                    if notification_type == "grace_12h":
                        user.notified_grace_12h = True
                        user.notified_expired = True

                    elif notification_type == "expired":
                        user.notified_expired = True

                except TelegramForbiddenError:
                    logger.info(
                        "User %s blocked the bot",
                        user.telegram_id,
                    )

                    user.is_bot_blocked = True

                except Exception as e:
                    user.notification_retry_count = retry_count + 1
                    user.last_notification_attempt = current_time

                    logger.warning(
                        "Failed to send post-expiry notification "
                        "to %s: %s",
                        user.telegram_id,
                        e,
                    )