import html
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.servers_repo import get_available_servers, get_server_by_id
from database.repositories.profiles_repo import get_user_profiles, get_profile_by_id, update_profile
from services.device_service import DeviceService
from bot.texts import (
    CONNECTION_LIST_HEADER, DEVICE_CARD, DEVICE_NOT_CONNECTED,
    DEVICE_RECENTLY_ACTIVE, ERROR_NO_SUBSCRIPTION, ERROR_DEVICE_LIMIT_REACHED,
    ERROR_SERVER_UNAVAILABLE, DOWNLOAD_CONF_FALLBACK
)
from bot.keyboards import get_device_keyboard, get_back_button, get_device_delete_confirm_keyboard
from bot.states import DeviceCreationStates, DeviceManagementStates
from utils.formatters import format_traffic, format_datetime
from utils.vpn_parser import parse_vpn_uri, is_valid_amneziawg_config
from utils.config_builder import build_amneziawg_config
from utils.admin import is_admin
from utils.telegram import safe
from database.models import User
from bot.constants import REPLY_MENU_BUTTONS

router = Router()
logger = logging.getLogger(__name__)


async def _build_connections_screen(user: User, session: AsyncSession) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    text = CONNECTION_LIST_HEADER.format(count=profiles_count, limit=user.device_limit)
    builder = InlineKeyboardBuilder()
    if profiles_count == 0:
        text += "_У вас пока нет подключённых устройств._"
    else:
        for profile in profiles:
            server = profile.server
            flag = server.country_flag if server else "🌍"
            server_name = server.name if server else "Неизвестно"
            builder.button(text=f"⚙️ {safe(profile.device_name)}", callback_data=f"manage_device:{profile.id}")
            traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)
            last_connected_text = (
                DEVICE_RECENTLY_ACTIVE.format(last_connected=format_datetime(profile.last_connected))
                if profile.last_connected else DEVICE_NOT_CONNECTED
            )
            text += DEVICE_CARD.format(
                device_name=safe(profile.device_name), flag=flag, server_name=safe(server_name),
                last_connected_text=last_connected_text,
                traffic_down=format_traffic(profile.traffic_down),
                traffic_up=format_traffic(profile.traffic_up),
                traffic_total=traffic_total
            )
    if profiles_count < user.device_limit:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")
    builder.adjust(1)
    return text, builder


@router.message(F.text == "🔌 Подключение")
async def show_connections(message: Message, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    user = db_user
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    from services.subscription import SubscriptionService
    if not await SubscriptionService.check_access(session, user.telegram_id):
        await message.answer(ERROR_NO_SUBSCRIPTION, parse_mode="HTML")
        return
    text, builder = await _build_connections_screen(user, session)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    text, builder = await _build_connections_screen(user, session)
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Устройство не найдено", show_alert=True)
        return
    server = await get_server_by_id(session, profile.server_id)
    flag = server.country_flag if server else "🌍"
    server_name = server.name if server else "Неизвестно"
    protocol = server.protocol if server else "—"
    text = (
        f"📱 <b>Управление устройством</b>\n"
        f"<b>{safe(profile.device_name)}</b>\n"
        f"📍 Локация: {flag} {safe(server_name)}\n"
        f"📡 Протокол: {protocol}\n"
        f"📊 Трафик: ∑ {format_traffic(profile.traffic_down + profile.traffic_up)}\n"
        f"⏱ Последняя активность: {format_datetime(profile.last_connected) if profile.last_connected else 'Нет данных'}\n"
        f"<i>Нажмите «🔑 Показать ключ», чтобы получить ключ подключения.</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_device_keyboard(profile.id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile or not db_user or profile.user_id != db_user.id:
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await callback.message.answer(
        f"🔑 <b>Ключ подключения для {safe(profile.device_name)}:</b>\n"
        f"<code>{safe(profile.raw_config)}</code>\n"
        f"<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ.</i>",
        parse_mode="HTML"
    )
    await callback.answer("✅ Ключ отправлен")


@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Профиль не найден", show_alert=True)
        return
    await callback.bot.send_chat_action(chat_id=callback.from_user.id, action="upload_document")
    safe_device_name = "".join(c for c in profile.device_name if c.isalnum() or c in (' ', '_', '-')).strip() or "client"
    parsed = parse_vpn_uri(profile.raw_config)
    if parsed is None or not is_valid_amneziawg_config(parsed):
        await callback.message.answer(
            DOWNLOAD_CONF_FALLBACK.format(device_name=safe(profile.device_name), raw_config=safe(profile.raw_config)),
            parse_mode="HTML"
        )
        await callback.answer("⚠️ Не удалось собрать .conf", show_alert=False)
        return
    conf_content = build_amneziawg_config(parsed)
    if conf_content is None:
        await callback.answer("⚠️ Ошибка сборки .conf", show_alert=False)
        return
    file_bytes = conf_content.encode("utf-8")
    input_file = BufferedInputFile(file_bytes, filename=f"{safe_device_name}.conf")
    protocol_badge = "AmneziaWG 2.0" if parsed.protocol == "amneziawg2" else "AmneziaWG"
    await callback.message.answer_document(
        document=input_file,
        caption=(
            f"📁 <b>Конфигурация {protocol_badge}</b>\n"
            f"📱 Устройство: <b>{safe(profile.device_name)}</b>\n"
            f"<i>Импортируйте файл в приложение AmneziaVPN.</i>"
        ),
        parse_mode="HTML"
    )
    await callback.answer("✅ .conf отправлен")


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Устройство не найдено", show_alert=True)
        return
    await state.update_data(profile_id=profile_id)
    await state.set_state(DeviceManagementStates.rename_device)
    await callback.message.edit_text(
        "✏️ Введите новое имя для устройства (макс. 16 символов, только латиница и цифры):",
        reply_markup=get_back_button(f"manage_device:{profile_id}")
    )
    await callback.answer()


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(message: Message, state: FSMContext, session: AsyncSession):
    import re
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("back_to_connections"))
        return
    new_name = message.text.strip()
    DEVICE_NAME_REGEX = re.compile(r'^[a-zA-Z0-9\s_-]+$')
    if not new_name or len(new_name) > 16 or not DEVICE_NAME_REGEX.match(new_name):
        await message.answer("⚠️ Некорректное имя (до 16 символов, латиница, цифры):")
        return
    data = await state.get_data()
    profile_id = data.get("profile_id")
    profile = await get_profile_by_id(session, profile_id)
    if profile:
        await update_profile(session, profile, device_name=new_name)
        await message.answer(
            f"✅ Устройство переименовано в <b>{safe(new_name)}</b>",
            reply_markup=get_back_button(f"manage_device:{profile_id}"), parse_mode="HTML"
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
    text = (
        f"⚠️ <b>Подтверждение удаления</b>\n"
        f"Вы уверены, что хотите удалить устройство:\n"
        f"📱 <b>{safe(profile.device_name)}</b>?\n"
        f"<i>После удаления вам нужно будет создать устройство заново.</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_device_delete_confirm_keyboard(profile_id), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    profile = await get_profile_by_id(session, profile_id)
    if not profile:
        await callback.answer("❌ Устройство не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        f"📱 <b>Управление устройством</b>\n<b>{safe(profile.device_name)}</b>",
        reply_markup=get_device_keyboard(profile.id), parse_mode="HTML"
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
    deleted = await DeviceService.delete_device(session, profile)
    if not deleted:
        await callback.answer("❌ Сервер недоступен. Попробуйте позже.", show_alert=True)
        return
    user = await get_user_by_telegram_id(session, callback.from_user.id)
    if user:
        text, builder = await _build_connections_screen(user, session)
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer("🗑 Устройство успешно удалено", show_alert=True)


@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    servers = await get_available_servers(session)
    if not servers:
        await callback.answer("❌ На всех серверах закончились свободные слоты.", show_alert=True)
        return
    text = "🌍 <b>Выберите локацию для подключения:</b>\n"
    builder = InlineKeyboardBuilder()
    for server in servers:
        flag = server.country_flag or "🌍"
        builder.button(text=f"{flag} {server.name}", callback_data=f"select_server:{server.id}")
    builder.button(text="← Назад", callback_data="back_to_connections")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(DeviceCreationStates.choose_server)
    await callback.answer()


@router.callback_query(F.data.startswith("select_server:"), DeviceCreationStates.choose_server)
async def select_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer("❌ Локация не найдена", show_alert=True)
        await state.clear()
        return
    await state.update_data(server_id=server_id)
    await state.set_state(DeviceCreationStates.enter_device_name)
    flag = server.country_flag or "🌍"
    await callback.message.edit_text(
        f"✏️ Введите имя устройства для {flag} {safe(server.name)}:\n"
        f"(например: IPhone, MacBook, Work PC)\n"
        f"Максимум 16 символов, только латиница и цифры.",
        reply_markup=get_back_button("add_device")
    )
    await callback.answer()


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(message: Message, state: FSMContext, session: AsyncSession, db_user: User | None = None):
    import re
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("back_to_connections"))
        return
    device_name = message.text.strip()
    DEVICE_NAME_REGEX = re.compile(r'^[a-zA-Z0-9\s_-]+$')
    if not device_name or len(device_name) > 16 or not DEVICE_NAME_REGEX.match(device_name):
        await message.answer("⚠️ Имя устройства должно быть от 1 до 16 символов (латиница, цифры, пробелы, дефисы):")
        return
    data = await state.get_data()
    server_id = data.get("server_id")
    user = db_user
    if not user:
        await message.answer("❌ Пользователь не найден.")
        await state.clear()
        return

    from database.repositories.profiles_repo import get_user_profiles_count
    profiles_count = await get_user_profiles_count(session, user.id)
    if profiles_count >= user.device_limit:
        await message.answer(ERROR_DEVICE_LIMIT_REACHED.format(limit=user.device_limit), parse_mode="HTML")
        await state.clear()
        return

    await message.bot.send_chat_action(chat_id=message.from_user.id, action="typing")
    profile = await DeviceService.create_device(session, user, server_id, device_name)
    if not profile:
        await message.answer(ERROR_SERVER_UNAVAILABLE, parse_mode="HTML")
        await state.clear()
        return

    await state.clear()
    server = await get_server_by_id(session, profile.server_id)
    await message.answer(
        f"✅ <b>Устройство добавлено!</b>\n"
        f"📱 {safe(device_name)} ({server.country_flag} {safe(server.name)})\n"
        f"<i>Используйте кнопки ниже, чтобы получить ключ подключения.</i>",
        reply_markup=get_device_keyboard(profile.id), parse_mode="HTML"
    )