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
from database.repositories.profiles_repo import get_profile_by_id, get_user_profiles, get_user_profiles_count, update_profile
from database.repositories.servers_repo import get_available_servers, get_server_by_id
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.tariffs_repo import get_tariff_by_id
from services.device_service import DeviceService
from services.subscription import SubscriptionService
from utils.formatters import format_datetime, format_traffic
from utils.telegram import safe, render_hub, send_hub_document
from utils.vpn_parser import build_vpn_file, build_conf_file, is_valid_vpn_uri

router = Router()
logger = logging.getLogger(__name__)
DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9\s_-]+$")

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
            traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)
            rendered += texts.DEVICE_CARD.format(
                device_name=safe(profile.device_name), flag=flag, server_name=safe(server_name),
                last_connected_text=last_connected_text, traffic_down=format_traffic(profile.traffic_down),
                traffic_up=format_traffic(profile.traffic_up), traffic_total=traffic_total,
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
    🔥 ИСПРАВЛЕНО: Отдача ДВУХ файлов (.vpn и .conf) + текстовый хаб с инструкцией.
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
    
    # 1. Отправляем .vpn через SMH (удаляет текстовый хаб)
    await send_hub_document(
        callback.bot, callback.message.chat.id,
        document=vpn_file,
        caption=f"📁 <b>Основной клиент Amnezia</b>\n📱 Устройство: <b>{safe(profile.device_name)}</b>\n<i>Для универсального приложения</i>",
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML"
    )
    
    # 2. Отправляем .conf отдельным сообщением
    await callback.bot.send_document(
        chat_id=callback.message.chat.id,
        document=conf_file,
        caption=f"📁 <b>AmneziaWG</b>\n📱 Устройство: <b>{safe(profile.device_name)}</b>\n<i>Для отдельного легковесного приложения</i>",
        parse_mode="HTML"
    )
    
    # 3. Отправляем текстовый хаб с инструкцией (станет новым SMH)
    instruction_text = (
        "✅ <b>Файлы конфигурации отправлены!</b>\n\n"
        "📥 <b>Как подключить:</b>\n"
        "1️⃣ <b>.vpn</b> — импортируйте в <b>основной клиент Amnezia</b>.\n"
        "2️⃣ <b>.conf</b> — импортируйте в <b>AmneziaWG</b>.\n\n"
        "<i>💡 Нажмите на файл выше, чтобы открыть его в нужном приложении.</i>"
    )
    await render_hub(
        callback.bot, callback.message.chat.id,
        instruction_text,
        get_back_button(f"manage_device:{profile.id}")
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
    await callback.answer("⏳ Удаляю устройство...")
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
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

@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    await state.clear()
    servers = await get_available_servers(session)
    if not servers:
        await callback.answer(texts.ERROR_NO_FREE_SLOTS, show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for server in servers:
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
    profile = await DeviceService.create_device(
        session, user, data.get("server_id"), device_name
    )
    if not profile:
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