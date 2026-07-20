import asyncio
import logging
import re
from datetime import timedelta

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import (
    get_back_button,
    get_device_delete_confirm_keyboard,
    get_device_keyboard,
)
from bot.states import DeviceCreationStates, DeviceManagementStates
from database.models import User
from database.repositories.profiles_repo import (
    get_profile_by_id,
    get_user_profiles,
    get_user_profiles_count,
    update_profile,
)
from database.repositories.servers_repo import (
    get_available_servers,
    get_server_by_id,
)
from database.repositories.tariffs_repo import get_tariff_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from services.device_service import (
    DailyLimitExceeded,
    DeviceLimitExceeded,
    DeviceService,
    InvalidConfig,
    ServerUnavailable,
)
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.formatters import (
    format_connection_device_card,
    format_datetime,
    format_traffic,
)
from utils.telegram import (
    append_hub_document,
    append_hub_message,
    delete_hub_ids,
    get_hub_ids,
    render_hub,
    safe,
)
from utils.vpn_parser import (
    build_conf_file_from_dict,
    build_vpn_file_from_dict,
    decode_vpn_uri_to_json,
)

router = Router()
logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9\s_-]+$")

_deleting_devices: set[int] = set()
_creating_devices: set[int] = set()

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
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            return tariff.device_limit
    return 0


def _get_grace_deletion_time(user: User):
    """
    Возвращает время, когда устройства пользователя будут удалены
    после истечения подписки.

    Правила:
    - если подписки нет — None;
    - если подписка вечная — None;
    - иначе subscription_end + 48 часов.
    """
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

            rendered += (
                "\n⚠️ <b>Подписка истекла</b>\n"
                "Устройства можно удалить, но они не будут работать.\n"
                f"Устройства будут удалены через: <b>{countdown}</b>\n"
                "Продлите доступ, чтобы сохранить их.\n"
            )
        else:
            rendered += (
                "\n⚠️ <b>Подписка истекла</b>\n"
                "Устройства можно удалить, но они не будут работать.\n"
            )

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


@router.callback_query(F.data == "menu_connections")
async def hub_menu_connections(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_connections(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_connections(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server = await get_server_by_id(session, profile.server_id)

    flag = server.country_flag if server else "🌍"
    server_name = server.name if server else "Неизвестно"
    protocol = _format_protocol(server.protocol if server else None)

    rendered = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name),
        flag=flag,
        server_name=safe(server_name),
        protocol=protocol,
        traffic_total=format_traffic(
            profile.traffic_down + profile.traffic_up,
        ),
        last_connected=(
            format_datetime(profile.last_connected)
            if profile.last_connected
            else "Нет данных"
        ),
    )

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if has_access:
        keyboard = get_device_keyboard(profile.id)
    else:
        rendered += (
            "\n⚠️ <b>Доступ неактивен</b>\n"
            "Ключ и файлы конфигурации недоступны.\n"
            "Устройство можно удалить.\n"
        )

        builder = InlineKeyboardBuilder()

        builder.button(
            text="🗑 Удалить устройство",
            callback_data=f"request_delete_device:{profile.id}",
        )

        builder.button(
            text="← К списку устройств",
            callback_data="back_to_connections",
        )

        builder.button(
            text="🏠 В главное меню",
            callback_data="back_to_main_menu",
        )

        builder.adjust(1)

        keyboard = builder.as_markup()

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        rendered,
        keyboard,
    )


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await callback.answer(
            "⚠️ Доступ неактивен. Продлите подписку.",
            show_alert=True,
        )
        return

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_SHOW_KEY.format(
            device_name=safe(profile.device_name),
            raw_config=safe(profile.raw_config),
        ),
        get_back_button(f"manage_device:{profile.id}"),
    )


@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await callback.answer(
            "⚠️ Доступ неактивен. Продлите подписку.",
            show_alert=True,
        )
        return

    await callback.answer("⏳ Генерирую файлы...")

    safe_device_name = "".join(
        c
        for c in profile.device_name
        if c.isalnum() or c in (" ", "_", "-")
    ).strip() or "client"

    decoded = decode_vpn_uri_to_json(profile.raw_config)

    if decoded is None:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(
                device_name=safe(profile.device_name),
            ),
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    vpn_content = build_vpn_file_from_dict(decoded)
    conf_content = build_conf_file_from_dict(decoded)

    if not vpn_content or not conf_content:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(
                device_name=safe(profile.device_name),
            ),
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    vpn_file = BufferedInputFile(
        vpn_content.encode("utf-8"),
        filename=f"{safe_device_name}.amnezia",
    )

    conf_file = BufferedInputFile(
        conf_content.encode("utf-8"),
        filename=f"{safe_device_name}.conf",
    )

    old_hub_ids = await get_hub_ids(callback.message.chat.id)

    await append_hub_document(
        callback.bot,
        callback.message.chat.id,
        document=vpn_file,
        caption=(
            f"📁 <b>Основной клиент Amnezia</b>\n"
            f"📱 Устройство: <b>{safe(profile.device_name)}</b>\n"
            f"<i>Для универсального приложения</i>"
        ),
        parse_mode="HTML",
    )

    await append_hub_document(
        callback.bot,
        callback.message.chat.id,
        document=conf_file,
        caption=(
            f"📁 <b>AmneziaWG</b>\n"
            f"📱 Устройство: <b>{safe(profile.device_name)}</b>\n"
            f"<i>Для отдельного легковесного приложения</i>"
        ),
        parse_mode="HTML",
    )

    instruction_text = (
        "✅ <b>Файлы конфигурации отправлены!</b>\n"
        "📥 <b>Как подключить:</b>\n"
        "1️⃣ Первый файл импортируйте в <b>основной клиент Amnezia</b>.\n"
        "2️⃣ Второй файл импортируйте в <b>AmneziaWG</b>.\n"
        "<i>💡 Нажмите на файл выше, чтобы открыть его "
        "в нужном приложении.</i>"
    )

    await append_hub_message(
        callback.bot,
        callback.message.chat.id,
        text=instruction_text,
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML",
    )

    await delete_hub_ids(
        callback.bot,
        callback.message.chat.id,
        old_hub_ids,
    )


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    has_access = await SubscriptionService.check_access(
        session,
        db_user.telegram_id,
    )

    if not has_access:
        await callback.answer(
            "⚠️ Доступ неактивен. Продлите подписку.",
            show_alert=True,
        )
        return

    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_RENAME_PROMPT,
        get_back_button(f"manage_device:{profile_id}"),
    )


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not message.text or message.text.startswith("/"):
        await state.clear()
        return

    new_name = message.text.strip()

    if (
        not new_name
        or len(new_name) > 16
        or not DEVICE_NAME_REGEX.match(new_name)
    ):
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_INVALID_DEVICE_NAME,
            get_back_button("back_to_connections"),
        )
        return

    data = await state.get_data()

    profile = await get_profile_by_id(
        session,
        data.get("profile_id"),
    )

    if profile:
        await update_profile(
            session,
            profile,
            device_name=new_name,
        )

        await render_hub(
            message.bot,
            message.chat.id,
            f"✅ Устройство переименовано в <b>{safe(new_name)}</b>",
            get_device_keyboard(profile.id),
        )

    await state.clear()


@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    profile = await get_profile_by_id(session, profile_id)

    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_DELETE_CONFIRM.format(
            device_name=safe(profile.device_name),
        ),
        get_device_delete_confirm_keyboard(profile_id),
    )


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer("❌ Удаление отменено")
    await state.clear()

    profile_id = int(callback.data.split(":")[1])

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        "📱 <b>Управление устройством</b>",
        get_device_keyboard(profile_id),
    )


@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    profile_id = int(callback.data.split(":")[1])

    if profile_id in _deleting_devices:
        await callback.answer(
            "⏳ Уже удаляем устройство...",
            show_alert=True,
        )
        return

    _deleting_devices.add(profile_id)

    try:
        await callback.answer("⏳ Удаляю устройство...")
        await state.clear()

        profile = await get_profile_by_id(session, profile_id)

        if (
            not profile
            or not db_user
            or profile.user_id != db_user.id
        ):
            await callback.answer(
                texts.ERROR_ACCESS_DENIED,
                show_alert=True,
            )
            return

        if not await DeviceService.delete_device(session, profile):
            await callback.answer(
                texts.ERROR_SERVER_UNAVAILABLE_GENERIC,
                show_alert=True,
            )
            return

        user = db_user or await get_user_by_telegram_id(
            session,
            callback.from_user.id,
        )

        if user:
            await _render_connections(
                callback.message,
                user,
                session,
            )

    finally:
        _deleting_devices.discard(profile_id)


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
    F.data.startswith("select_server:"),
    DeviceCreationStates.choose_server,
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