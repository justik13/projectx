import html
import logging
import uuid
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.servers_repo import get_active_servers, get_server_by_id
from database.repositories.profiles_repo import (
    get_user_profiles, get_user_profiles_count, create_profile, delete_profile, get_profile_by_id, update_profile
)
from services.subscription import SubscriptionService
from services.amnezia_client import AmneziaClient
from bot.texts import (
    CONNECTION_LIST_HEADER, DEVICE_CARD, DEVICE_NOT_CONNECTED,
    DEVICE_RECENTLY_ACTIVE, ERROR_NO_SUBSCRIPTION, ERROR_DEVICE_LIMIT_REACHED,
    ERROR_SERVER_UNAVAILABLE
)
from bot.keyboards import get_device_keyboard, get_back_button, get_device_delete_confirm_keyboard
from bot.states import DeviceCreationStates, DeviceManagementStates
from utils.formatters import format_traffic, format_datetime
from database.models import User

router = Router()
logger = logging.getLogger(__name__)
DEVICE_NAME_REGEX = re.compile(r'^[a-zA-Z0-9\s_-]+$')
REPLY_MENU_BUTTONS = ["👤 Профиль", "🔌 Подключение", "💳 Оплата", "💬 Поддержка", "🛠 Админка"]


async def _build_connections_screen(user: User, session) -> tuple[str, InlineKeyboardBuilder]:
    profiles = await get_user_profiles(session, user.id)
    profiles_count = len(profiles)
    text = CONNECTION_LIST_HEADER.format(count=profiles_count, limit=user.device_limit)
    builder = InlineKeyboardBuilder()
    if profiles_count == 0:
        text += "\n_У вас пока нет подключённых устройств._"
    else:
        for profile in profiles:
            server = profile.server
            flag = server.country_flag if server else "🌍"
            server_name = server.name if server else "Неизвестно"
            safe_device_name = html.escape(profile.device_name)
            safe_server_name = html.escape(server_name)
            builder.button(text=f"⚙️ Настройки: {safe_device_name}", callback_data=f"manage_device:{profile.id}")
            traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)
            last_connected_text = (
                DEVICE_RECENTLY_ACTIVE.format(last_connected=format_datetime(profile.last_connected))
                if profile.last_connected else DEVICE_NOT_CONNECTED
            )
            text += DEVICE_CARD.format(
                device_name=safe_device_name, flag=flag, server_name=safe_server_name,
                last_connected_text=last_connected_text,
                traffic_down=format_traffic(profile.traffic_down),
                traffic_up=format_traffic(profile.traffic_up),
                traffic_total=traffic_total
            ) + "\n"
    if profiles_count < user.device_limit:
        builder.button(text="➕ Добавить устройство", callback_data="add_device")
    builder.button(text="← Назад", callback_data="back_to_main_menu")
    builder.adjust(1)
    return text, builder


@router.message(F.text == "🔌 Подключение")
async def show_connections(message: Message, state: FSMContext, db_user: User | None = None):
    await state.clear()
    user = db_user
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    session = await get_session()
    try:
        if not await SubscriptionService.check_access(session, user.telegram_id):
            await message.answer(ERROR_NO_SUBSCRIPTION)
            return
        text, builder = await _build_connections_screen(user, session)
        await message.answer(text, reply_markup=builder.as_markup())
    finally:
        await session.close()


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(callback: CallbackQuery, state: FSMContext, db_user: User | None = None):
    await state.clear()
    user = db_user
    if not user:
        await callback.answer("❌ Пользователь не найден", show_alert=True)
        return
    session = await get_session()
    try:
        text, builder = await _build_connections_screen(user, session)
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer()
    except Exception as e:
        logger.error(f"Error returning to connections: {e}")
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("manage_device:"))
async def manage_device(callback: CallbackQuery, state: FSMContext):
    """Карточка устройства"""
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer("❌ Устройство не найдено", show_alert=True)
            return
        server = await get_server_by_id(session, profile.server_id)
        flag = server.country_flag if server else "🌍"
        server_name = server.name if server else "Неизвестно"
        protocol = server.protocol if server else "—"
        safe_device_name = html.escape(profile.device_name)
        safe_server_name = html.escape(server_name)
        text = (
            f"📱 Управление устройством: <b>{safe_device_name}</b>\n"
            f"─────────────────────────────\n"
            f"📍 Локация: {flag} {safe_server_name}\n"
            f"📡 Протокол: {protocol}\n"
            f"📊 Трафик: ∑ {format_traffic(profile.traffic_down + profile.traffic_up)}\n"
            f"⏱ Последняя активность: {format_datetime(profile.last_connected) if profile.last_connected else 'Нет данных'}\n\n"
            f"<i>Нажмите «🔑 Показать ключ», чтобы получить ключ подключения.</i>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=get_device_keyboard(profile.id),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("show_config:"))
async def show_config(callback: CallbackQuery, state: FSMContext, db_user: User | None = None):
    """Показать ключ подключения с возможностью копирования тапом"""
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        user = db_user
        if not profile:
            await callback.answer("❌ Конфигурация не найдена.", show_alert=True)
            return
        if not user or profile.user_id != user.id:
            await callback.answer("⛔️ Нет доступа", show_alert=True)
            return
            
        # Отправляем отдельным сообщением. Тег <code> позволяет копировать текст в один тап.
        await callback.message.answer(
            f"🔑 <b>Ключ подключения для {html.escape(profile.device_name)}:</b>\n\n"
            f"<code>{html.escape(profile.raw_config)}</code>\n\n"
            f"<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>",
            parse_mode="HTML"
        )
        await callback.answer("✅ Ключ отправлен")
    finally:
        await session.close()


@router.callback_query(F.data.startswith("download_conf:"))
async def download_conf(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer("❌ Профиль не найден", show_alert=True)
            return
        await callback.bot.send_chat_action(
            chat_id=callback.from_user.id,
            action="upload_document"
        )
        file_bytes = profile.raw_config.encode("utf-8")
        safe_device_name = "".join(c for c in profile.device_name if c.isalnum() or c in (' ', '_', '-')).strip()
        input_file = BufferedInputFile(file_bytes, filename=f"{safe_device_name or 'client'}.conf")
        await callback.message.answer_document(
            document=input_file,
            caption=f"📁 Файл конфигурации для устройства <b>{html.escape(profile.device_name)}</b>",
            parse_mode="HTML"
        )
        await callback.answer("✅ Файл отправлен")
    finally:
        await session.close()


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(callback: CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
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
    finally:
        await session.close()


@router.message(DeviceManagementStates.rename_device)
async def rename_device_process(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("back_to_connections"))
        return
    new_name = message.text.strip()
    if not new_name or len(new_name) > 16 or not DEVICE_NAME_REGEX.match(new_name):
        await message.answer("⚠️ Некорректное имя. Используйте только латинские буквы, цифры, пробелы и дефисы (до 16 символов):")
        return
    data = await state.get_data()
    profile_id = data.get("profile_id")
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if profile:
            await update_profile(session, profile, device_name=new_name)
            await message.answer(
                f"✅ Устройство успешно переименовано в <b>{html.escape(new_name)}</b>",
                reply_markup=get_back_button(f"manage_device:{profile_id}"), 
                parse_mode="HTML"
            )
            await state.clear()
    finally:
        await session.close()


# ═══════════════════════════════════════════════════════════════
# УДАЛЕНИЕ УСТРОЙСТВА С ПОДТВЕРЖДЕНИЕМ
# ═══════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("request_delete_device:"))
async def request_delete_device(callback: CallbackQuery, state: FSMContext):
    """Показать экран подтверждения удаления"""
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer("❌ Устройство уже удалено", show_alert=True)
            return
        safe_device_name = html.escape(profile.device_name)
        text = (
            f"⚠️ <b>Подтверждение удаления</b>\n"
            f"─────────────────────────────\n"
            f"Вы уверены, что хотите удалить устройство:\n"
            f"📱 <b>{safe_device_name}</b>?\n\n"
            f"<i>После удаления вам нужно будет создать устройство заново и обновить конфиг в приложении.</i>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=get_device_delete_confirm_keyboard(profile_id),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("cancel_delete_device:"))
async def cancel_delete_device(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления — возврат к карточке устройства"""
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer("❌ Устройство не найдено", show_alert=True)
            return
        server = await get_server_by_id(session, profile.server_id)
        flag = server.country_flag if server else "🌍"
        server_name = server.name if server else "Неизвестно"
        protocol = server.protocol if server else "—"
        safe_device_name = html.escape(profile.device_name)
        safe_server_name = html.escape(server_name)
        text = (
            f"📱 Управление устройством: <b>{safe_device_name}</b>\n"
            f"─────────────────────────────\n"
            f"📍 Локация: {flag} {safe_server_name}\n"
            f"📡 Протокол: {protocol}\n"
            f"📊 Трафик: ∑ {format_traffic(profile.traffic_down + profile.traffic_up)}\n"
            f"⏱ Последняя активность: {format_datetime(profile.last_connected) if profile.last_connected else 'Нет данных'}"
        )
        await callback.message.edit_text(
            text,
            reply_markup=get_device_keyboard(profile.id),
            parse_mode="HTML"
        )
        await callback.answer("❌ Удаление отменено")
    finally:
        await session.close()


@router.callback_query(F.data.startswith("confirm_delete_device:"))
async def confirm_delete_device(callback: CallbackQuery, state: FSMContext):
    """Подтверждённое удаление устройства"""
    await state.clear()
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        profile = await get_profile_by_id(session, profile_id)
        if not profile:
            await callback.answer("❌ Устройство уже удалено", show_alert=True)
            return

        await callback.answer("⏳ Удаляю устройство...", show_alert=False)
        server = await get_server_by_id(session, profile.server_id)
        if server:
            client = AmneziaClient(server.api_url, server.api_key)
            deleted = await client.delete_user(client_id=profile.peer_id)
            if not deleted:
                await callback.answer("❌ Сервер недоступен. Попробуйте удалить устройство позже.", show_alert=True)
                return
        else:
            logger.warning(f"Deleting orphan profile {profile.id} (server not found)")

        await delete_profile(session, profile)
        user = await get_user_by_telegram_id(session, callback.from_user.id)
        if not user:
            return
        text, builder = await _build_connections_screen(user, session)
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await callback.answer("🗑 Устройство успешно удалено", show_alert=True)
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        await callback.answer("❌ Ошибка при удалении устройства на сервере", show_alert=True)
    finally:
        await session.close()


# ═══════════════════════════════════════════════════════════════
# СОЗДАНИЕ УСТРОЙСТВА
# ═══════════════════════════════════════════════════════════════

@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    session = await get_session()
    try:
        servers = await get_active_servers(session)
        if not servers:
            await callback.answer("❌ Нет доступных локаций", show_alert=True)
            return
        text = "🌍 Выберите локацию для подключения:\n"
        builder = InlineKeyboardBuilder()
        for server in servers:
            flag = server.country_flag or "🌍"
            builder.button(text=f"{flag} {server.name}", callback_data=f"select_server:{server.id}")
        builder.button(text="← Назад", callback_data="back_to_connections")
        builder.adjust(1)
        await callback.message.edit_text(text, reply_markup=builder.as_markup())
        await state.set_state(DeviceCreationStates.choose_server)
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("select_server:"), DeviceCreationStates.choose_server)
async def select_server(callback: CallbackQuery, state: FSMContext):
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Локация не найдена", show_alert=True)
            await state.clear()
            return
        await state.update_data(server_id=server_id)
        await state.set_state(DeviceCreationStates.enter_device_name)
        flag = server.country_flag or "🌍"
        await callback.message.edit_text(
            f"✏️ Введите имя устройства для {flag} {html.escape(server.name)}:\n"
            f"(например: IPhone, MacBook, Work PC)\nМаксимум 16 символов, только латиница и цифры.",
            reply_markup=get_back_button("add_device")
        )
        await callback.answer()
    finally:
        await session.close()


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(message: Message, state: FSMContext, db_user: User | None = None):
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
        
    # ПРАВИЛЬНЫЙ блок прерывания операции
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("back_to_connections"))
        return
        
    device_name = message.text.strip()
    if not device_name or len(device_name) > 16 or not DEVICE_NAME_REGEX.match(device_name):
        await message.answer("⚠️ Имя устройства должно быть от 1 до 16 символов (только латиница, цифры, пробелы и дефисы):")
        return
        
    data = await state.get_data()
    server_id = data.get("server_id")
    session = await get_session()
    try:
        user = db_user
        if not user:
            await message.answer("❌ Пользователь не найден.")
            await state.clear()
            return
        server = await get_server_by_id(session, server_id)
        if server.protocol != "amneziawg2":
            await message.answer("⚠️ Сервер использует неподдерживаемый протокол.")
            await state.clear()
            return
            
        profiles_count = await get_user_profiles_count(session, user.id)
        if profiles_count >= user.device_limit:
            await message.answer(ERROR_DEVICE_LIMIT_REACHED.format(limit=user.device_limit))
            await state.clear()
            return

        await message.bot.send_chat_action(
            chat_id=message.from_user.id,
            action="typing"
        )
        short_hash = uuid.uuid4().hex[:4]
        clean_device_name = re.sub(r'[^a-zA-Z0-9]', '', device_name)[:10]
        client_name = f"tg_{user.telegram_id}_{clean_device_name}_{short_hash}"
        expires_ts = await SubscriptionService.get_expires_timestamp(user)
        client = AmneziaClient(server.api_url, server.api_key)
        result = await client.create_user(client_name=client_name, expires_at=expires_ts)
        
        if not result or not result.get("id") or not result.get("config"):
            await message.answer(ERROR_SERVER_UNAVAILABLE)
            await state.clear()
            return
            
        try:
            profile = await create_profile(
                session, user_id=user.id, server_id=server.id,
                device_name=device_name, peer_id=result.get("id"),
                raw_config=result.get("config")
            )
        except Exception as e:
            logger.error(f"Failed to create profile in DB: {e}")
            try:
                await client.delete_user(client_id=result.get("id"))
            except Exception as rollback_error:
                logger.error(f"Failed to rollback: {rollback_error}")
            await message.answer("❌ Произошла ошибка при создании устройства. Попробуйте еще раз.")
            await state.clear()
            return
            
        await state.clear()
        
        # ПРАВИЛЬНЫЙ блок успешного создания
        await message.answer(
            f"✅ <b>Устройство добавлено!</b>\n"
            f"📱 {html.escape(device_name)} ({server.country_flag} {html.escape(server.name)})\n\n"
            f"🔑 <b>Ключ подключения:</b>\n"
            f"<code>{html.escape(profile.raw_config)}</code>\n\n"
            f"<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ в буфер обмена.</i>",
            reply_markup=get_device_keyboard(profile.id),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error creating device: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при создании устройства.")
        await state.clear()
    finally:
        await session.close()