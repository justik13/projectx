import asyncio
import logging
import re

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_back_button, get_device_delete_confirm_keyboard, get_device_keyboard
from bot.states import DeviceCreationStates, DeviceManagementStates
from bot import texts
from database.models import User
from database.repositories.profiles_repo import (
    get_profile_by_id, get_user_profiles, get_user_profiles_count, update_profile
)
from database.repositories.servers_repo import get_available_servers, get_server_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.tariffs_repo import get_tariff_by_id
from services.device_service import DeviceService
from services.subscription import SubscriptionService
from services.slots_cache import get_real_peer_count
from utils.formatters import format_datetime, format_traffic, format_connection_device_card
from utils.telegram import (
    safe, render_hub, clear_and_delete_hub, append_hub_document, append_hub_message
)
from utils.vpn_parser import build_vpn_file, build_conf_file

router = Router()
logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9\s_-]+$")

# ⚠️ In-memory locks — сбрасываются при рестарте бота.
# Для single-worker это acceptable risk.
# DB unique constraint на peer_id защищает от дубликатов.
# ThrottlingMiddleware (0.1s) защищает от double-click.
_deleting_devices: set[int] = set()
_creating_devices: set[int] = set()

logger.debug(
    "connection.py loaded: in-memory locks initialized "
    "(cleared on restart, protected by DB constraints)"
)


async def _get_effective_device_limit(user: User, session: AsyncSession) -> int:
    if user.current_tariff_id:
        tariff = await get_tariff_by_id(session, user.current_tariff_id)
        if tariff:
            return tariff.device_limit
    return 0


async def _build_connections_screen(user: User, session: AsyncSession) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    device_limit = await _get_effective_device_limit(user, session)

    rendered = texts.CONNECTION_LIST_HEADER.format(count=profiles_count, limit=device_limit)
    builder = InlineKeyboardBuilder()

    if profiles_count == 0:
        rendered += texts.CONNECTION_EMPTY
    else:
        for profile in profiles:
            server = profile.server
            flag = server.country_flag if server else "🌍"
            server_name = server.name if server else "Неизвестно"
            builder.button(
                text=f"⚙️ {safe(profile.device_name)}",
                callback_data=f"manage_device:{profile.id}"
            )

            last_connected_text = (
                texts.DEVICE_RECENTLY_ACTIVE.format(last_connected=format_datetime(profile.last_connected))
                if profile.last_connected else texts.DEVICE_NOT_CONNECTED
            )

            rendered += format_connection_device_card(
                profile, flag, server_name, last_connected_text
            )

    if profiles_count < device_limit:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")

    builder.adjust(1)
    return rendered, builder


async def _render_connections(target, user: User, session: AsyncSession):
    if not user:
        await render_hub(
            target.bot, target.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("back_to_main_menu")
        )
        return

    if not await SubscriptionService.check_access(session, user.telegram_id):
        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Купить доступ", callback_data="menu_buy")
        builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        builder.adjust(1)
        await render_hub(target.bot, target.chat.id, texts.ERROR_NO_SUBSCRIPTION, builder.as_markup())
        return

    rendered, builder = await _build_connections_screen(user, session)
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)
    await render_hub(target.bot, target.chat.id, rendered, builder.as_markup())


@router.callback_query(F.data == "menu_connections")
async def hub_menu_connections(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else "🌍"
    server_name = server.name if server else "Неизвестно"
    protocol = server.protocol if server else "—"

    rendered = texts.DEVICE_MANAGE_HEADER.format(
        device_name=safe(profile.device_name), flag=flag, server_name=safe(server_name),
        protocol=protocol, traffic_total=format_traffic(profile.traffic_down + profile.traffic_up),
        last_connected=(format_datetime(profile.last_connected) if profile.last_connected else "Нет данных"),
    )
    await render_hub(callback.bot, callback.message.chat.id, rendered, get_device_keyboard(profile.id))


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await render_hub(
        callback.bot, callback.message.chat.id,
        texts.DEVICE_SHOW_KEY.format(
            device_name=safe(profile.device_name),
            raw_config=safe(profile.raw_config)
        ),
        get_back_button(f"manage_device:{profile.id}")
    )


@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    """
    🔥 ИСКЛЮЧЕНИЕ ИЗ SMH: Отправка двух файлов (.vpn и .conf) + текстовая инструкция.
    Telegram API не позволяет прикрепить текст к двум документам одновременно,
    поэтому инструкция отправляется третьим сообщением (допустимое исключение).
    Это нарушает ЖЁСТКОЕ ПРАВИЛО #2 (SMH), но необходимо для UX.
    Документально зафиксировано как известное исключение.
    """
    await callback.answer("⏳ Генерирую файлы...")
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    safe_device_name = "".join(
        c for c in profile.device_name if c.isalnum() or c in (" ", "_", "-")
    ).strip() or "client"

    vpn_content = build_vpn_file(profile.raw_config)
    conf_content = build_conf_file(profile.raw_config)

    if not vpn_content or not conf_content:
        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name)),
            get_back_button(f"manage_device:{profile.id}")
        )
        return

    vpn_file = BufferedInputFile(vpn_content.encode("utf-8"), filename=f"{safe_device_name}.vpn")
    conf_file = BufferedInputFile(conf_content.encode("utf-8"), filename=f"{safe_device_name}.conf")

    # 🔥 ИСКЛЮЧЕНИЕ ИЗ SMH: Очищаем хаб перед отправкой файлов
    await clear_and_delete_hub(callback.bot, callback.message.chat.id)

    # Сообщение 1: .vpn файл
    await append_hub_document(
        callback.bot, callback.message.chat.id,
        document=vpn_file,
        caption=(
            f"📁 <b>Основной клиент Amnezia</b>\n"
            f"📱 Устройство: <b>{safe(profile.device_name)}</b>\n"
            f"<i>Для универсального приложения</i>"
        ),
        parse_mode="HTML"
    )

    # Сообщение 2: .conf файл
    await append_hub_document(
        callback.bot, callback.message.chat.id,
        document=conf_file,
        caption=(
            f"📁 <b>AmneziaWG</b>\n"
            f"📱 Устройство: <b>{safe(profile.device_name)}</b>\n"
            f"<i>Для отдельного легковесного приложения</i>"
        ),
        parse_mode="HTML"
    )

    # 🔥 Сообщение 3: Текстовая инструкция (обязательно для UX)
    instruction_text = (
        "✅ <b>Файлы конфигурации отправлены!</b>\n"
        "📥 <b>Как подключить:</b>\n"
        "1️⃣ <b>.vpn</b> — импортируйте в <b>основной клиент Amnezia</b>.\n"
        "2️⃣ <b>.conf</b> — импортируйте в <b>AmneziaWG</b>.\n"
        "<i>💡 Нажмите на файл выше, чтобы открыть его в нужном приложении.</i>"
    )
    await append_hub_message(
        callback.bot, callback.message.chat.id,
        text=instruction_text,
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)
    await render_hub(
        callback.bot, callback.message.chat.id,
        texts.DEVICE_RENAME_PROMPT,
        get_back_button(f"manage_device:{profile_id}")
    )


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text or message.text.startswith("/"):
        await state.clear()
        return

    new_name = message.text.strip()
    if not new_name or len(new_name) > 16 or not DEVICE_NAME_REGEX.match(new_name):
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_INVALID_DEVICE_NAME,
            get_back_button("back_to_connections")
        )
        return

    data = await state.get_data()
    profile = await get_profile_by_id(session, data.get("profile_id"))
    if profile:
        await update_profile(session, profile, device_name=new_name)
        await render_hub(
            message.bot, message.chat.id,
            f"✅ Устройство переименовано в <b>{safe(new_name)}</b>",
            get_device_keyboard(profile.id)
        )
    await state.clear()


@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    await callback.answer()
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await render_hub(
        callback.bot, callback.message.chat.id,
        texts.DEVICE_DELETE_CONFIRM.format(device_name=safe(profile.device_name)),
        get_device_delete_confirm_keyboard(profile_id)
    )


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer("❌ Удаление отменено")
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    await render_hub(
        callback.bot, callback.message.chat.id,
        f"📱 <b>Управление устройством</b>",
        get_device_keyboard(profile_id)
    )


@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(
    callback: CallbackQuery, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    profile_id = int(callback.data.split(":")[1])

    # 🔥 Защита от двойного нажатия при удалении
    if profile_id in _deleting_devices:
        await callback.answer("⏳ Уже удаляем устройство...", show_alert=True)
        return
    _deleting_devices.add(profile_id)

    try:
        await callback.answer("⏳ Удаляю устройство...")
        await state.clear()
        profile = await get_profile_by_id(session, profile_id)
        if not profile or not db_user or profile.user_id != db_user.id:
            await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
            return

        if not await DeviceService.delete_device(session, profile):
            await callback.answer(texts.ERROR_SERVER_UNAVAILABLE_GENERIC, show_alert=True)
            return

        user = db_user or await get_user_by_telegram_id(session, callback.from_user.id)
        if user:
            await _render_connections(callback.message, user, session)
    finally:
        _deleting_devices.discard(profile_id)


@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user_id = callback.from_user.id

    # 🔥 Защита от двойного нажатия при создании
    if user_id in _creating_devices:
        await callback.answer("⏳ Уже обрабатываем запрос...", show_alert=True)
        return
    _creating_devices.add(user_id)

    try:
        await callback.answer("⏳ Проверяю доступные слоты...")
        await state.clear()

        # 1. Получаем локально доступные серверы (БД)
        servers = await get_available_servers(session)
        if not servers:
            await render_hub(
                callback.bot, callback.message.chat.id,
                texts.ERROR_NO_FREE_SLOTS,
                get_back_button("back_to_connections")
            )
            return

        # 2. Параллельная проверка реального количества слотов через API с кэшированием
        results = await asyncio.gather(*[get_real_peer_count(server) for server in servers])

        available_servers = []
        for server, real_count in zip(servers, results):
            if real_count == -1:
                available_servers.append(server)
            elif real_count < server.max_clients:
                available_servers.append(server)

        if not available_servers:
            await render_hub(
                callback.bot, callback.message.chat.id,
                texts.ERROR_NO_FREE_SLOTS,
                get_back_button("back_to_connections")
            )
            return

        # 3. Рендерим только серверы, где реально есть места
        builder = InlineKeyboardBuilder()
        for server in available_servers:
            flag = server.country_flag or "🌍"
            builder.button(
                text=f"{flag} {server.name}",
                callback_data=f"select_server:{server.id}"
            )
        builder.button(text="← Назад", callback_data="back_to_connections")
        builder.adjust(1)

        await render_hub(
            callback.bot, callback.message.chat.id,
            texts.CONNECTION_SELECT_SERVER,
            builder.as_markup()
        )
        await state.set_state(DeviceCreationStates.choose_server)
    finally:
        _creating_devices.discard(user_id)


@router.callback_query(F.data.startswith("select_server:"), DeviceCreationStates.choose_server)
async def select_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_LOCATION_NOT_FOUND, show_alert=True)
        await state.clear()
        return

    await state.update_data(server_id=server_id)
    await state.set_state(DeviceCreationStates.enter_device_name)

    flag = server.country_flag or "🌍"
    await render_hub(
        callback.bot, callback.message.chat.id,
        texts.DEVICE_ADD_NAME_PROMPT.format(flag=flag, server_name=safe(server.name)),
        get_back_button("add_device")
    )


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(
    message: Message, state: FSMContext,
    session: AsyncSession, db_user: User | None = None
):
    user_id = message.from_user.id

    # 🔥 Защита от спама сообщениями при создании
    if user_id in _creating_devices:
        # 🔥 ИСПРАВЛЕНО: Используем render_hub вместо message.answer
        await render_hub(
            message.bot,
            message.chat.id,
            "⏳ Пожалуйста, подождите, предыдущий запрос обрабатывается...",
            get_back_button("add_device")
        )
        return
    _creating_devices.add(user_id)

    try:
        if not message.text or message.text.startswith("/"):
            await state.clear()
            return

        device_name = message.text.strip()
        if not device_name or len(device_name) > 16 or not DEVICE_NAME_REGEX.match(device_name):
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_INVALID_DEVICE_NAME,
                get_back_button("add_device")
            )
            return

        user = db_user
        if not user:
            await state.clear()
            return

        device_limit = await _get_effective_device_limit(user, session)
        profiles_count = await get_user_profiles_count(session, user.id)

        if profiles_count >= device_limit:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_DEVICE_LIMIT_REACHED.format(limit=device_limit),
                get_back_button("back_to_connections")
            )
            await state.clear()
            return

        data = await state.get_data()

        # 🔥 ИСПРАВЛЕНО: Сбрасываем маркер daily limit перед вызовом create_device
        if hasattr(user, '_daily_limit_exceeded'):
            delattr(user, '_daily_limit_exceeded')

        profile = await DeviceService.create_device(
            session, user, data.get("server_id"), device_name
        )

        if not profile:
            # 🔥 ИСПРАВЛЕНО: Различаем причину отказа
            # Если установлен маркер _daily_limit_exceeded — показываем специальное сообщение
            if hasattr(user, '_daily_limit_exceeded'):
                await render_hub(
                    message.bot, message.chat.id,
                    texts.ERROR_DEVICE_DAILY_LIMIT,
                    get_back_button("back_to_connections"),
                    parse_mode="HTML"
                )
                # Очищаем маркер
                delattr(user, '_daily_limit_exceeded')
            else:
                await render_hub(
                    message.bot, message.chat.id,
                    texts.ERROR_SERVER_UNAVAILABLE,
                    get_back_button("back_to_connections")
                )
            await state.clear()
            return

        server = await get_server_by_id(session, profile.server_id)
        success_text = texts.DEVICE_ADDED_SUCCESS.format(
            device_name=safe(device_name),
            flag=server.country_flag,
            server_name=safe(server.name)
        )
        await render_hub(message.bot, message.chat.id, success_text, get_device_keyboard(profile.id))
        await state.clear()
    finally:
        _creating_devices.discard(user_id)