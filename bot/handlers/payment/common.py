import logging
from decimal import Decimal, InvalidOperation

from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_tariff_showcase_keyboard,
)
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from services.maintenance_service import MaintenanceService
from utils.datetime_helpers import is_expired
from utils.formatters import format_datetime, format_days_left
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub

logger = logging.getLogger(__name__)


PAYMENT_MANUAL_REVIEW_TEXT = (
    "💳 <b>Оплата получена</b>\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Мы проверяем платёж.\n"
    "Если доступ не активировался в течение нескольких минут, "
    "напишите в поддержку.\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "<i>Обычно проверка занимает не более 5 минут.</i>"
)


def _to_decimal(value) -> Decimal | None:
    """
    Безопасно конвертирует значение в Decimal.

    Использовать для финансовых данных.
    Никогда не использовать float-сравнения для денег.
    """
    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def _is_subscription_active(user) -> bool:
    if not user or not user.subscription_end:
        return False

    return not is_expired(user.subscription_end)


async def _get_effective_device_limit(
    session: AsyncSession,
    user,
) -> int:
    """
    Возвращает актуальный лимит устройств пользователя.

    Приоритет:
    1. тариф из current_tariff_id;
    2. user.device_limit как fallback.
    """
    if user is None:
        return 0

    current_tariff_id = getattr(user, "current_tariff_id", None)

    if current_tariff_id:
        tariff = await get_tariff_by_id(
            session,
            current_tariff_id,
        )

        if tariff:
            return getattr(tariff, "device_limit", 0) or 0

    return getattr(user, "device_limit", 0) or 0


async def _render_maintenance(
    callback: CallbackQuery,
    session: AsyncSession,
    *,
    back_to: str = "back_to_main_menu",
) -> None:
    message = await MaintenanceService.get_message(session)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        message,
        get_back_button(back_to),
    )


async def _check_tariff_change_allowed(
    session: AsyncSession,
    db_user,
    tariff,
) -> str | None:
    """
    Проверяет, можно ли пользователю купить/сменить тариф.

    Запрещает:
    - даунгрейд во время активной подписки;
    - покупку тарифа, если устройств больше нового лимита.
    """
    new_limit = getattr(tariff, "device_limit", 2)

    is_active = await _is_subscription_active(db_user)

    if is_active:
        current_limit = await _get_effective_device_limit(
            session,
            db_user,
        )

        if new_limit < current_limit:
            return texts.PAYMENT_DOWNGRADE_BLOCKED.format(
                current_limit=current_limit,
                new_limit=new_limit,
                valid_until=format_datetime(db_user.subscription_end),
            )

    profiles_count = await get_user_profiles_count(
        session,
        db_user.id,
    )

    if profiles_count > new_limit:
        return texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
            profiles_count=profiles_count,
            new_limit=new_limit,
        )

    return None


async def _show_showcase(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    tariffs = await get_active_tariffs(session)

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("back_to_main_menu"),
        )
        return

    grouped: dict[int, list] = {}

    for tariff in tariffs:
        limit = getattr(tariff, "device_limit", 2)

        if limit not in grouped:
            grouped[limit] = []

        grouped[limit].append(tariff)

    keyboard = get_tariff_showcase_keyboard(grouped)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.PAYMENT_SHOWCASE_HEADER,
        keyboard,
    )


async def _show_hub(
    callback: CallbackQuery,
    user,
    session: AsyncSession,
) -> None:
    profiles = await get_user_profiles(session, user.id)

    device_limit = await _get_effective_device_limit(
        session,
        user,
    )

    tariff_name = get_tariff_display_name(device_limit)

    text = texts.PAYMENT_HUB_HEADER.format(
        valid_until=format_datetime(user.subscription_end),
        days_left=format_days_left(user.subscription_end),
        tariff_name=tariff_name,
        devices_count=len(profiles),
        device_limit=device_limit,
    )

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🔄 Продлить доступ",
        callback_data="payment_quick_renew",
    )

    builder.button(
        text="⚙️ Сменить тариф",
        callback_data="payment_change_tariff",
    )

    builder.button(
        text="👤 Профиль",
        callback_data="menu_profile",
    )

    builder.button(
        text="🏠 В главное меню",
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 1, 1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )