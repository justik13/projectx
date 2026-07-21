from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_change_tariff_keyboard,
    get_payment_method_keyboard,
    get_renew_keyboard,
    get_tariff_duration_keyboard,
)
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariffs_repo import (
    get_active_tariffs,
    get_tariff_by_id,
)
from services.maintenance_service import MaintenanceService
from utils.formatters import format_datetime
from utils.tariff_names import get_tariff_display_name
from utils.telegram import render_hub

from .common import (
    _check_tariff_change_allowed,
    _is_subscription_active,
    _render_maintenance,
    _show_hub,
    _show_showcase,
)

router = Router()


@router.callback_query(F.data.in_(["menu_buy", "menu_subscription"]))
async def hub_menu_payment(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    session: AsyncSession = None,
) -> None:
    await callback.answer()
    await state.clear()

    if not db_user:
        return

    if session is None:
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback,
            session,
            back_to="back_to_main_menu",
        )
        return

    is_active = await _is_subscription_active(db_user)

    if is_active:
        await _show_hub(callback, db_user, session)
    else:
        await _show_showcase(callback, session)


@router.callback_query(F.data == "payment_showcase")
async def show_tariff_showcase_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    await callback.answer()

    if session is None:
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback,
            session,
            back_to="back_to_main_menu",
        )
        return

    await _show_showcase(callback, session)


@router.callback_query(F.data.startswith("select_tariff:"))
async def select_tariff(
    callback: CallbackQuery,
    state: FSMContext,
    db_user=None,
    session: AsyncSession = None,
) -> None:
    if session is None:
        await callback.answer()
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await callback.answer()

        await _render_maintenance(
            callback,
            session,
            back_to="payment_showcase",
        )
        return

    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)

    if not tariff or not tariff.is_active:
        await callback.answer(
            texts.ERROR_TARIFF_UNAVAILABLE,
            show_alert=True,
        )
        return

    device_limit = getattr(tariff, "device_limit", 2)

    if db_user:
        error_text = await _check_tariff_change_allowed(
            session,
            db_user,
            tariff,
        )

        if error_text:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                error_text,
                get_back_button("payment_change_tariff"),
            )

            await callback.answer()
            return

    tariff_name = get_tariff_display_name(device_limit)

    text = texts.PAYMENT_CHECKOUT_TEXT.format(
        tariff_name=tariff_name,
        duration_days=tariff.duration_days,
        price_rub=tariff.price_rub,
        price_stars=tariff.price_stars,
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        get_payment_method_keyboard(tariff.id, device_limit),
    )

    await callback.answer()


@router.callback_query(F.data.in_(["payment_quick_renew", "payment_renew"]))
async def show_quick_renew(
    callback: CallbackQuery,
    db_user,
    session: AsyncSession,
) -> None:
    await callback.answer()

    if session is None:
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback,
            session,
            back_to="menu_subscription",
        )
        return

    tariffs = await get_active_tariffs(session)

    current_limit = db_user.device_limit

    renew_tariffs = [
        tariff
        for tariff in tariffs
        if getattr(tariff, "device_limit", 2) == current_limit
    ]

    if not renew_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    tariff_name = get_tariff_display_name(current_limit)

    text = texts.PAYMENT_QUICK_RENEW_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_renew_keyboard(renew_tariffs)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data == "payment_change_tariff")
async def show_change_tariff(
    callback: CallbackQuery,
    db_user,
    session: AsyncSession,
) -> None:
    await callback.answer()

    if session is None:
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback,
            session,
            back_to="menu_subscription",
        )
        return

    tariffs = await get_active_tariffs(session)

    if not tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("menu_subscription"),
        )
        return

    current_limit = db_user.device_limit

    tariff_name = get_tariff_display_name(current_limit)

    is_active = await _is_subscription_active(db_user)

    text = texts.PAYMENT_CHANGE_TARIFF_HEADER.format(
        tariff_name=tariff_name,
        valid_until=format_datetime(db_user.subscription_end),
    )

    keyboard = get_change_tariff_keyboard(
        tariffs,
        current_limit,
        is_subscription_active=is_active,
    )

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )


@router.callback_query(F.data.startswith("select_tariff_type:"))
async def select_tariff_type(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user=None,
) -> None:
    await callback.answer()

    if session is None:
        return

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback,
            session,
            back_to="payment_showcase",
        )
        return

    device_limit = int(callback.data.split(":")[1])

    # Дополнительная серверная проверка даунгрейда и лимита устройств.
    if db_user:
        is_active = await _is_subscription_active(db_user)

        if is_active:
            current_limit = db_user.device_limit or 0

            if device_limit < current_limit:
                await render_hub(
                    callback.bot,
                    callback.message.chat.id,
                    texts.PAYMENT_DOWNGRADE_BLOCKED.format(
                        current_limit=current_limit,
                        new_limit=device_limit,
                        valid_until=format_datetime(
                            db_user.subscription_end,
                        ),
                    ),
                    get_back_button("payment_change_tariff"),
                )
                return

        profiles_count = await get_user_profiles_count(
            session,
            db_user.id,
        )

        if profiles_count > device_limit:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.PAYMENT_DOWNGRADE_BLOCKED_PROFILES.format(
                    profiles_count=profiles_count,
                    new_limit=device_limit,
                ),
                get_back_button("payment_change_tariff"),
            )
            return

    tariffs = await get_active_tariffs(session)

    type_tariffs = [
        tariff
        for tariff in tariffs
        if getattr(tariff, "device_limit", 2) == device_limit
    ]

    if not type_tariffs:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.PAYMENT_NO_TARIFFS,
            get_back_button("payment_showcase"),
        )
        return

    description = texts.PAYMENT_TARIFF_DESCRIPTION.get(
        device_limit,
        "",
    )

    text = description + texts.PAYMENT_DURATION_HEADER

    keyboard = get_tariff_duration_keyboard(type_tariffs)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        keyboard,
    )