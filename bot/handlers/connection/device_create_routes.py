import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button, get_device_keyboard
from bot.states import DeviceCreationStates
from database.models import User
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DailyLimitExceeded,
    DeviceLimitExceeded,
    DeviceService,
    InvalidConfig,
    NoActiveSubscription,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from utils.telegram import render_hub, safe

from .common import (
    DEVICE_NAME_REGEX,
    _get_effective_device_limit,
    _render_maintenance,
)

router = Router()
logger = logging.getLogger(__name__)

_creating_devices: set[int] = set()


@router.callback_query(F.data == "add_device")
async def start_add_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(
        session,
        user_id,
    ):
        await callback.answer()
        await _render_maintenance(
            callback.message,
            session,
            back_to="back_to_connections",
        )
        return

    if user_id in _creating_devices:
        await callback.answer(
            "⏳ Уже обрабатываем запрос...",
            show_alert=True,
        )
        return

    user = db_user or await get_user_by_telegram_id(
        session,
        user_id,
    )

    if not user or not await SubscriptionService.check_access(
        session,
        user.telegram_id,
    ):
        await callback.answer(
            texts.ERROR_NO_SUBSCRIPTION,
            show_alert=True,
        )
        return

    _creating_devices.add(user_id)

    try:
        await callback.answer()
        await state.clear()

        servers = await get_available_servers(session)

        if not servers:
            await render_hub(
                callback.bot,
                callback.message.chat.id,
                texts.ERROR_NO_FREE_SLOTS,
                get_back_button("back_to_connections"),
            )
            return

        builder = InlineKeyboardBuilder()

        for server in servers:
            flag = server.country_flag or "🌍"

            builder.button(
                text=f"{flag} {server.name}",
                callback_data=f"select_server:{server.id}",
            )

        builder.button(
            text="← Назад",
            callback_data="back_to_connections",
        )

        builder.adjust(1)

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.CONNECTION_SELECT_SERVER,
            builder.as_markup(),
        )

        await state.set_state(DeviceCreationStates.choose_server)

    finally:
        _creating_devices.discard(user_id)


@router.callback_query(
    StateFilter(DeviceCreationStates.choose_server),
    F.data.startswith("select_server:"),
)
async def select_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()

    if not await MaintenanceService.can_user_perform_action(
        session,
        callback.from_user.id,
    ):
        await _render_maintenance(
            callback.message,
            session,
            back_to="back_to_connections",
        )
        await state.clear()
        return

    user = db_user or await get_user_by_telegram_id(
        session,
        callback.from_user.id,
    )

    if not user or not await SubscriptionService.check_access(
        session,
        user.telegram_id,
    ):
        await callback.answer(
            texts.ERROR_NO_SUBSCRIPTION,
            show_alert=True,
        )
        await state.clear()
        return

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_LOCATION_NOT_FOUND,
            show_alert=True,
        )
        await state.clear()
        return

    if not server.is_active:
        await callback.answer(
            "⚠️ Сервер временно недоступен. "
            "Выберите другую локацию.",
            show_alert=True,
        )
        await state.clear()
        return

    await state.update_data(server_id=server_id)
    await state.set_state(DeviceCreationStates.enter_device_name)

    flag = server.country_flag or "🌍"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_ADD_NAME_PROMPT.format(
            flag=flag,
            server_name=safe(server.name),
        ),
        get_back_button("add_device"),
    )


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    user_id = message.from_user.id

    if not await MaintenanceService.can_user_perform_action(
        session,
        user_id,
    ):
        await _render_maintenance(
            message,
            session,
            back_to="back_to_connections",
        )
        await state.clear()
        return

    user = db_user or await get_user_by_telegram_id(
        session,
        user_id,
    )

    if not user or not await SubscriptionService.check_access(
        session,
        user.telegram_id,
    ):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NO_SUBSCRIPTION,
            get_back_button("back_to_connections"),
        )
        await state.clear()
        return

    if user_id in _creating_devices:
        await render_hub(
            message.bot,
            message.chat.id,
            "⏳ Пожалуйста, подождите, "
            "предыдущий запрос обрабатывается...",
            get_back_button("add_device"),
        )
        return

    _creating_devices.add(user_id)

    try:
        if not message.text or message.text.startswith("/"):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_TEXT_REQUIRED,
                get_back_button("add_device"),
            )
            return

        device_name = message.text.strip()

        if (
            not device_name
            or len(device_name) > 16
            or not DEVICE_NAME_REGEX.match(device_name)
        ):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_INVALID_DEVICE_NAME,
                get_back_button("add_device"),
            )
            return

        data = await state.get_data()
        server_id = data.get("server_id")

        if not server_id:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_UNAVAILABLE,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return

        await render_hub(
            message.bot,
            message.chat.id,
            "⏳ <b>Создаю устройство...</b>\n"
            "<i>Обычно это занимает несколько секунд.</i>",
            get_back_button("add_device"),
            parse_mode="HTML",
        )

        try:
            profile = await DeviceService.create_device(
                session,
                user,
                server_id,
                device_name,
            )

        except NoActiveSubscription:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_NO_SUBSCRIPTION,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return

        except DailyLimitExceeded:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_DEVICE_DAILY_LIMIT,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        except DeviceLimitExceeded:
            device_limit = await _get_effective_device_limit(
                user,
                session,
            )

            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_DEVICE_LIMIT_REACHED.format(
                    limit=device_limit,
                ),
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return

        except (ServerUnavailable, InvalidConfig):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_UNAVAILABLE,
                get_back_button("back_to_connections"),
            )
            await state.clear()
            return

        except Exception as e:
            logger.error(
                f"Unexpected error in enter_device_name: {e}",
                exc_info=True,
            )

            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_TECHNICAL_MESSAGE,
                get_back_button("back_to_connections"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        server = await get_server_by_id(
            session,
            profile.server_id,
        )

        success_text = texts.DEVICE_ADDED_SUCCESS.format(
            device_name=safe(device_name),
            flag=server.country_flag if server else "🌍",
            server_name=safe(server.name) if server else "—",
        )

        await render_hub(
            message.bot,
            message.chat.id,
            success_text,
            get_device_keyboard(profile.id),
        )

        await state.clear()

    finally:
        _creating_devices.discard(user_id)