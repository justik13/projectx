import logging
import re
from datetime import timedelta

from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from database.models import User
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.formatters import (
    format_connection_device_card,
    format_datetime,
    format_traffic,
)
from utils.telegram import render_hub, safe

logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9\s_-]+$")

_PROTOCOL_DISPLAY = {
    "amneziawg2": "AmneziaWG 2.0",
}

GRACE_PERIOD_HOURS = 48


def _format_protocol(raw_protocol: str | None) -> str:
    if not raw_protocol:
        return "—"
    return _PROTOCOL_DISPLAY.get(raw_protocol, raw_protocol)


async def _get_effective_device_limit(
    user: User,
    session: AsyncSession,
) -> int:
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            user.current_tariff_id,
        )
        if tariff:
            return tariff.device_limit

    return user.device_limit or 0


def _get_grace_deletion_time(user: User):
    if not user.subscription_end:
        return None
    if user.subscription_end.year >= 2100:
        return None
    return user.subscription_end + timedelta(
        hours=GRACE_PERIOD_HOURS,
    )


def _format_grace_countdown(deletion_time) -> str:
    if not deletion_time:
        return "в ближайшее время"

    current_time = now_utc()
    delta = deletion_time - current_time

    if delta.total_seconds() <= 0:
        return "в ближайшее время"

    days = delta.days
    hours = delta.seconds // 3600

    if days > 0:
        return f"{days} дн. {hours} ч."

    minutes = (delta.seconds % 3600) // 60
    return f"{hours} ч. {minutes} мин."


async def _render_maintenance(
    target,
    session: AsyncSession,
    *,
    back_to: str = "back_to_connections",
) -> None:
    message = await MaintenanceService.get_message(session)
    await render_hub(
        target.bot,
        target.chat.id,
        message,
        get_back_button(back_to),
    )


async def _build_connections_screen(
    user: User,
    session: AsyncSession,
    *,
    read_only: bool = False,
) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    device_limit = await _get_effective_device_limit(
        user,
        session,
    )

    rendered = texts.CONNECTION_LIST_HEADER.format(
        count=profiles_count,
        limit=device_limit,
    )

    if read_only:
        deletion_time = _get_grace_deletion_time(user)
        if deletion_time:
            countdown = _format_grace_countdown(deletion_time)
            rendered += texts.CONNECTION_EXPIRED_READ_ONLY.format(
                countdown=countdown,
            )
        else:
            rendered += texts.CONNECTION_EXPIRED_NO_GRACE

    builder = InlineKeyboardBuilder()

    if profiles_count == 0:
        rendered += texts.CONNECTION_EMPTY
    else:
        for profile in profiles:
            server = profile.server
            flag = server.country_flag if server else "🌍"
            server_name = server.name if server else "Неизвестно"

            if read_only:
                builder.button(
                    text=f"🔒 {safe(profile.device_name)}",
                    callback_data=f"manage_device:{profile.id}",
                )
            else:
                builder.button(
                    text=f"⚙️ {safe(profile.device_name)}",
                    callback_data=f"manage_device:{profile.id}",
                )

            last_connected_text = (
                texts.DEVICE_RECENTLY_ACTIVE.format(
                    last_connected=format_datetime(
                        profile.last_connected,
                    ),
                )
                if profile.last_connected
                else texts.DEVICE_NOT_CONNECTED
            )

            rendered += format_connection_device_card(
                profile,
                flag,
                server_name,
                last_connected_text,
            )

    if not read_only and profiles_count < device_limit:
        builder.button(
            text="➕ Добавить устройство",
            callback_data="add_device",
        )

    builder.adjust(1)
    return rendered, builder


async def _render_connections(
    target,
    user: User,
    session: AsyncSession,
):
    if not user:
        await render_hub(
            target.bot,
            target.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("back_to_main_menu"),
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        user.telegram_id,
    )

    profiles_count = await get_user_profiles_count(
        session,
        user.id,
    )

    if not has_access:
        if profiles_count > 0:
            rendered, builder = await _build_connections_screen(
                user,
                session,
                read_only=True,
            )

            builder.button(
                text="🚀 Купить доступ",
                callback_data="menu_buy",
            )
            builder.button(
                text="🏠 В главное меню",
                callback_data="back_to_main_menu",
            )
            builder.adjust(1)

            await render_hub(
                target.bot,
                target.chat.id,
                rendered,
                builder.as_markup(),
            )
            return

        builder = InlineKeyboardBuilder()
        builder.button(
            text="🚀 Купить доступ",
            callback_data="menu_buy",
        )
        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )
        builder.adjust(1)

        await render_hub(
            target.bot,
            target.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            builder.as_markup(),
        )
        return

    rendered, builder = await _build_connections_screen(
        user,
        session,
        read_only=False,
    )

    builder.button(
        text="🏠 В главное меню",
        callback_data="back_to_main_menu",
    )
    builder.adjust(1)

    await render_hub(
        target.bot,
        target.chat.id,
        rendered,
        builder.as_markup(),
    )