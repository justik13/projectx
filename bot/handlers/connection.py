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
from services.device_service import DeviceService
from services.subscription import SubscriptionService
from utils.config_builder import build_amneziawg_config
from utils.formatters import format_datetime, format_traffic
from utils.telegram import safe
from utils.vpn_parser import is_valid_amneziawg_config, parse_vpn_uri

router = Router()
logger = logging.getLogger(__name__)

DEVICE_NAME_REGEX = re.compile(r"^[a-zA-Z0-9\s_-]+$")

async def _build_connections_screen(user: User, session: AsyncSession) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    rendered = texts.CONNECTION_LIST_HEADER.format(count=profiles_count, limit=user.device_limit)
    builder = InlineKeyboardBuilder()

    if profiles_count == 0:
        rendered += texts.CONNECTION_EMPTY
    else:
        for profile in profiles:
            server = profile.server
            flag = server.country_flag if server else "🌍"
            server_name = server.name if server else "Неизвестно"
            builder.button(text=f"⚙️ {safe(profile.device_name)}", callback_data=f"manage_device:{profile.id}")

            last_connected_text = texts.DEVICE_RECENTLY_ACTIVE.format(last_connected=format_datetime(profile.last_connected)) if profile.last_connected else texts.DEVICE_NOT_CONNECTED
            traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)
            rendered += texts.DEVICE_CARD.format(
                device_name=safe(profile.device_name), flag=flag, server_name=safe(server_name),
                last_connected_text=last_connected_text, traffic_down=format_traffic(profile.traffic_down),
                traffic_up=format_traffic(profile.traffic_up), traffic_total=traffic_total,
            )

    if profiles_count < user.device_limit:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")

    builder.adjust(1)
    return rendered, builder

async def _render_connections(target, user: User, session: AsyncSession, *, edit: bool):
    if not user:
        kb = get_back_button("back_to_main_menu")
        if edit: await target.edit_text(texts.ERROR_USER_NOT_FOUND, reply_markup=kb)
        else: await target.answer(texts.ERROR_USER_NOT_FOUND, reply_markup=kb)
        return

    if not await SubscriptionService.check_access(session, user.telegram_id):
        builder = InlineKeyboardBuilder()
        builder.button(text="🚀 Купить доступ", callback_data="menu_buy")
        builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        builder.adjust(1)
        kb = builder.as_markup()
        if edit: await target.edit_text(texts.ERROR_NO_SUBSCRIPTION, reply_markup=kb, parse_mode="HTML")
        else: await target.answer(texts.ERROR_NO_SUBSCRIPTION, reply_markup=kb, parse_mode="HTML")
        return

    rendered, builder = await _build_connections_screen(user, session)
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)

    if edit:
        try: await target.edit_text(rendered, reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception: pass
    else:
        await target.answer(rendered, reply_markup=builder.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "menu_connections")
async def hub_menu_connections(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_connections(callback.message, db_user, session, edit=True)
    await callback.answer()

@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return
    await _render_connections(callback.message, db_user, session, edit=True)
    await callback.answer()

@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
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
    await callback.message.edit_text(rendered, reply_markup=get_device_keyboard(profile.id), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("show_config:"))
async def show_config(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.message.edit_text(
        texts.DEVICE_SHOW_KEY.format(device_name=safe(profile.device_name), raw_config=safe(profile.raw_config)),
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML",
    )
    await callback.answer("✅ Ключ отображен")

@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
        return

    await callback.bot.send_chat_action(chat_id=callback.from_user.id, action="upload_document")
    await callback.message.edit_text("⏳ <b>Генерируем конфигурацию...</b>", parse_mode="HTML")

    safe_device_name = "".join(c for c in profile.device_name if c.isalnum() or c in (" ", "_", "-")).strip() or "client"
    parsed = parse_vpn_uri(profile.raw_config)

    if parsed is None or not is_valid_amneziawg_config(parsed):
        await callback.message.edit_text(
            texts.DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name), raw_config=safe(profile.raw_config)),
            reply_markup=get_back_button(f"manage_device:{profile.id}"),
            parse_mode="HTML",
        )
        await callback.answer("⚠️ Не удалось собрать .conf", show_alert=False)
        return

    conf_content = build_amneziawg_config(parsed)
    if conf_content is None:
        await callback.message.edit_text(
            "⚠️ Ошибка сборки .conf",
            reply_markup=get_back_button(f"manage_device:{profile.id}"),
            parse_mode="HTML"
        )
        await callback.answer("⚠️ Ошибка сборки .conf", show_alert=False)
        return

    input_file = BufferedInputFile(conf_content.encode("utf-8"), filename=f"{safe_device_name}.conf")
    protocol_badge = "AmneziaWG 2.0" if parsed.protocol == "amneziawg2" else "AmneziaWG"
    
    await callback.message.answer_document(
        document=input_file,
        caption=texts.DEVICE_CONF_CAPTION.format(protocol_badge=protocol_badge, device_name=safe(profile.device_name)),
        parse_mode="HTML",
    )
    
    await callback.message.edit_text(
        "✅ <b>Файл успешно отправлен ниже.</b>\n\nИспользуйте кнопки для навигации.",
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML"
    )
    await callback.answer("✅ .conf отправлен")

@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(texts.ERROR_PROFILE_NOT_FOUND, show_alert=True)
        return

    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)
    await callback.message.edit_text(texts.DEVICE_RENAME_PROMPT, reply_markup=get_back_button(f"manage_device:{profile_id}"))
    await callback.answer()

@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text:
        await message.answer(texts.ERROR_TEXT_REQUIRED)
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer(texts.ERROR_OPERATION_CANCELLED, reply_markup=get_back_button("back_to_connections"))
        return

    new_name = message.text.strip()
    if not new_name or len(new_name) > 16 or not DEVICE_NAME_REGEX.match(new_name):
        await message.answer(texts.ERROR_INVALID_DEVICE_NAME)
        return

    data = await state.get_data()
    profile = await get_profile_by_id(session, data.get("profile_id"))
    if profile:
        await update_profile(session, profile, device_name=new_name)
        await message.answer(
            f"✅ Устройство переименовано в <b>{safe(new_name)}</b>",
            reply_markup=get_back_button(f"manage_device:{profile.id}"), parse_mode="HTML",
        )
    await state.clear()

@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Устройство уже удалено", show_alert=True)
        return

    await callback.message.edit_text(
        texts.DEVICE_DELETE_CONFIRM.format(device_name=safe(profile.device_name)),
        reply_markup=get_device_delete_confirm_keyboard(profile_id), parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer(texts.ERROR_DEVICE_NOT_FOUND, show_alert=True)
        return

    await callback.message.edit_text(
        f"📱 <b>Управление устройством</b>\n\n<b>{safe(profile.device_name)}</b>",
        reply_markup=get_device_keyboard(profile.id), parse_mode="HTML",
    )
    await callback.answer("❌ Удаление отменено")

@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Устройство уже удалено", show_alert=True)
        return

    await callback.answer("⏳ Удаляю устройство...", show_alert=False)

    if not await DeviceService.delete_device(session, profile):
        await callback.answer(texts.ERROR_SERVER_UNAVAILABLE_GENERIC, show_alert=True)
        return

    user = await get_user_by_telegram_id(session, callback.from_user.id)
    if user:
        await _render_connections(callback.message, user, session, edit=True)
    await callback.answer("🗑 Устройство успешно удалено", show_alert=True)

@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    servers = await get_available_servers(session)
    if not servers:
        await callback.answer(texts.ERROR_NO_FREE_SLOTS, show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for server in servers:
        flag = server.country_flag or "🌍"
        builder.button(text=f"{flag} {server.name}", callback_data=f"select_server:{server.id}")
    builder.button(text="← Назад", callback_data="back_to_connections")
    builder.adjust(1)

    await callback.message.edit_text(texts.CONNECTION_SELECT_SERVER, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(DeviceCreationStates.choose_server)
    await callback.answer()

@router.callback_query(F.data.startswith("select_server:"), DeviceCreationStates.choose_server)
async def select_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_LOCATION_NOT_FOUND, show_alert=True)
        await state.clear()
        return

    await state.update_data(server_id=server_id)
    await state.set_state(DeviceCreationStates.enter_device_name)

    flag = server.country_flag or "🌍"
    await callback.message.edit_text(
        texts.DEVICE_ADD_NAME_PROMPT.format(flag=flag, server_name=safe(server.name)),
        reply_markup=get_back_button("add_device"),
    )
    await callback.answer()

@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(message: Message, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    if not message.text:
        await message.answer(texts.ERROR_TEXT_REQUIRED)
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer(texts.ERROR_OPERATION_CANCELLED, reply_markup=get_back_button("back_to_connections"))
        return

    device_name = message.text.strip()
    if not device_name or len(device_name) > 16 or not DEVICE_NAME_REGEX.match(device_name):
        await message.answer(texts.ERROR_INVALID_DEVICE_NAME)
        return

    user = db_user
    if not user:
        await message.answer(texts.ERROR_USER_NOT_FOUND)
        await state.clear()
        return

    if await get_user_profiles_count(session, user.id) >= user.device_limit:
        await message.answer(texts.ERROR_DEVICE_LIMIT_REACHED.format(limit=user.device_limit), parse_mode="HTML")
        await state.clear()
        return

    await message.bot.send_chat_action(chat_id=message.from_user.id, action="typing")

    data = await state.get_data()
    profile = await DeviceService.create_device(session, user, data.get("server_id"), device_name)
    if not profile:
        await message.answer(texts.ERROR_SERVER_UNAVAILABLE, parse_mode="HTML")
        await state.clear()
        return

    server = await get_server_by_id(session, profile.server_id)
    await state.clear()
    await message.answer(
        texts.DEVICE_ADDED_SUCCESS.format(device_name=safe(device_name), flag=server.country_flag, server_name=safe(server.name)),
        reply_markup=get_device_keyboard(profile.id), parse_mode="HTML",
    )