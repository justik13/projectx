from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.connection import get_session
from database.repositories.users_repo import get_user_by_telegram_id
from database.repositories.servers_repo import get_active_servers, get_server_by_id
from database.repositories.profiles_repo import get_user_profiles, get_user_profiles_count, create_profile, delete_profile, get_profile_by_id
from services.subscription import SubscriptionService
from services.amnezia_client import AmneziaClient
from bot.texts import CONNECTION_LIST_HEADER, DEVICE_CARD, DEVICE_NOT_CONNECTED, DEVICE_RECENTLY_ACTIVE, ERROR_NO_SUBSCRIPTION, ERROR_DEVICE_LIMIT_REACHED, ERROR_SERVER_UNAVAILABLE
from bot.keyboards import get_connection_keyboard, get_device_keyboard, get_back_button
from bot.states import DeviceCreationStates
from utils.formatters import format_traffic, format_datetime
from config.settings import get_settings
import logging
import uuid

router = Router()


@router.message(F.text == "🔌 Подключение")
async def show_connections(message: Message):
    """Показать список устройств пользователя"""
    telegram_id = message.from_user.id
    session = await get_session()

    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await message.answer("❌ Пользователь не найден.")
            return

        has_access = await SubscriptionService.check_access(session, telegram_id)
        if not has_access:
            await message.answer(ERROR_NO_SUBSCRIPTION)
            return

        profiles = await get_user_profiles(session, user.id)
        profiles_count = len(profiles)

        text = CONNECTION_LIST_HEADER.format(
            count=profiles_count,
            limit=user.device_limit
        )

        if profiles_count == 0:
            text += "\n_У вас пока нет подключённых устройств._"
        else:
            for profile in profiles:
                server = await get_server_by_id(session, profile.server_id)
                flag = server.country_flag or "🌍" if server else "🌍"
                server_name = server.name if server else "Неизвестно"

                traffic_down = format_traffic(profile.traffic_down)
                traffic_up = format_traffic(profile.traffic_up)
                traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)

                if profile.last_connected:
                    last_connected_text = DEVICE_RECENTLY_ACTIVE.format(
                        last_connected=format_datetime(profile.last_connected)
                    )
                else:
                    last_connected_text = DEVICE_NOT_CONNECTED

                text += DEVICE_CARD.format(
                    device_name=profile.device_name,
                    flag=flag,
                    server_name=server_name,
                    last_connected_text=last_connected_text,
                    traffic_down=traffic_down,
                    traffic_up=traffic_up,
                    traffic_total=traffic_total
                )
                text += "\n"

        can_add = profiles_count < user.device_limit
        await message.answer(
            text,
            reply_markup=get_connection_keyboard(has_subscription=can_add)
        )
    finally:
        await session.close()


@router.callback_query(F.data == "add_device")
async def start_add_device(callback: CallbackQuery, state: FSMContext):
    """Начать процесс добавления устройства — выбор локации"""
    session = await get_session()

    try:
        servers = await get_active_servers(session)

        if not servers:
            await callback.answer("❌ Нет доступных локаций", show_alert=True)
            return

        text = "🌍 Выберите локацию для подключения:\n\n"
        builder = InlineKeyboardBuilder()

        for server in servers:
            flag = server.country_flag or "🌍"
            builder.button(
                text=f"{flag} {server.name}",
                callback_data=f"select_server:{server.id}"
            )

        builder.button(text="← Назад", callback_data="back_to_connections")
        builder.adjust(1)

        await callback.message.edit_text(
            text,
            reply_markup=builder.as_markup()
        )
        await state.set_state(DeviceCreationStates.choose_server)
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("select_server:"), DeviceCreationStates.choose_server)
async def select_server(callback: CallbackQuery, state: FSMContext):
    """Пользователь выбрал локацию — просим ввести имя устройства"""
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
            f"✏️ Введите имя устройства для {flag} {server.name}:\n\n"
            f"(например: IPhone, MacBook, Work PC)\n\n"
            f"Максимум 16 символов, только буквы и цифры.",
            reply_markup=get_back_button("add_device")
        )
        await callback.answer()
    finally:
        await session.close()


@router.message(DeviceCreationStates.enter_device_name)
async def enter_device_name(message: Message, state: FSMContext):
    """Пользователь ввёл имя устройства — создаём профиль"""
    device_name = message.text.strip()

    if not device_name or len(device_name) > 16:
        await message.answer("⚠️ Имя устройства должно быть от 1 до 16 символов. Попробуйте ещё раз:")
        return

    if not device_name.replace(" ", "").isalnum():
        await message.answer("⚠️ Используйте только буквы и цифры. Попробуйте ещё раз:")
        return

    data = await state.get_data()
    server_id = data.get("server_id")

    if not server_id:
        await message.answer("❌ Ошибка: локация не выбрана. Начните сначала.")
        await state.clear()
        return

    telegram_id = message.from_user.id
    session = await get_session()

    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        server = await get_server_by_id(session, server_id)

        if not user or not server:
            await message.answer("❌ Ошибка данных. Начните сначала.")
            await state.clear()
            return

        profiles_count = await get_user_profiles_count(session, user.id)
        if profiles_count >= user.device_limit:
            await message.answer(ERROR_DEVICE_LIMIT_REACHED.format(limit=user.device_limit))
            await state.clear()
            return

        # Формируем уникальное имя клиента для Amnezia API
        # Формат: tg_{user_id}_{device_name}_{short_hash}
        # Пример: tg_872658825_iPhone_a3f9
        short_hash = uuid.uuid4().hex[:4]
        # Убираем пробелы и спецсимволы из device_name для API
        clean_device_name = "".join(c for c in device_name if c.isalnum())[:10]
        client_name = f"tg_{telegram_id}_{clean_device_name}_{short_hash}"
        
        # Вызываем amnezia-api (API сам сгенерирует peer ID)
        client = AmneziaClient(server.api_url, server.api_key)
        result = await client.create_user(
            client_name=client_name,
            protocol=server.protocol,
            expires_at=None
        )

        if not result:
            await message.answer(ERROR_SERVER_UNAVAILABLE)
            await state.clear()
            return

        # Получаем данные от API
        peer_id = result.get("id")
        raw_config = result.get("config", "")

        if not peer_id or not raw_config:
            await message.answer(ERROR_SERVER_UNAVAILABLE)
            await state.clear()
            return

        # Сохраняем профиль в БД
        profile = await create_profile(
            session,
            user_id=user.id,
            server_id=server.id,
            device_name=device_name,
            peer_id=peer_id,
            raw_config=raw_config
        )

        await state.clear()

        flag = server.country_flag or "🌍"
        builder = InlineKeyboardBuilder()
        builder.button(text="📋 Скопировать ключ", callback_data=f"copy_config:{profile.id}")
        builder.button(text="🔌 К списку устройств", callback_data="back_to_connections")
        builder.adjust(1)

        await message.answer(
            f"✅ Устройство добавлено!\n\n"
            f"📱 {device_name} ({flag} {server.name})\n\n"
            f"Ключ подключения готов к использованию.",
            reply_markup=builder.as_markup()
        )

    except Exception as e:
        logging.error(f"Error creating device: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при создании устройства. Попробуйте позже.")
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("copy_config:"))
async def copy_config(callback: CallbackQuery):
    """Отправить конфигурацию устройства"""
    profile_id = int(callback.data.split(":")[1])
    session = await get_session()

    try:
        profile = await get_profile_by_id(session, profile_id)

        if not profile:
            await callback.answer("❌ Устройство не найдено", show_alert=True)
            return

        user = await get_user_by_telegram_id(session, callback.from_user.id)
        if not user or profile.user_id != user.id:
            await callback.answer("⛔️ Нет доступа", show_alert=True)
            return

        await callback.message.answer(
            f"🔑 Ключ подключения для {profile.device_name}:\n\n"
            f"<code>{profile.raw_config}</code>",
            parse_mode="HTML"
        )
        await callback.answer("✅ Ключ отправлен сообщением выше")
    finally:
        await session.close()


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку устройств"""
    await state.clear()
    try:
        await callback.message.delete()
    except:
        pass

    telegram_id = callback.from_user.id
    session = await get_session()

    try:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            await callback.answer("❌ Пользователь не найден", show_alert=True)
            return

        profiles = await get_user_profiles(session, user.id)
        profiles_count = len(profiles)

        text = CONNECTION_LIST_HEADER.format(
            count=profiles_count,
            limit=user.device_limit
        )

        if profiles_count == 0:
            text += "\n_У вас пока нет подключённых устройств._"
        else:
            for profile in profiles:
                server = await get_server_by_id(session, profile.server_id)
                flag = server.country_flag or "🌍" if server else "🌍"
                server_name = server.name if server else "Неизвестно"

                traffic_down = format_traffic(profile.traffic_down)
                traffic_up = format_traffic(profile.traffic_up)
                traffic_total = format_traffic(profile.traffic_down + profile.traffic_up)

                if profile.last_connected:
                    last_connected_text = DEVICE_RECENTLY_ACTIVE.format(
                        last_connected=format_datetime(profile.last_connected)
                    )
                else:
                    last_connected_text = DEVICE_NOT_CONNECTED

                text += DEVICE_CARD.format(
                    device_name=profile.device_name,
                    flag=flag,
                    server_name=server_name,
                    last_connected_text=last_connected_text,
                    traffic_down=traffic_down,
                    traffic_up=traffic_up,
                    traffic_total=traffic_total
                )
                text += "\n"

        can_add = profiles_count < user.device_limit
        await callback.message.answer(
            text,
            reply_markup=get_connection_keyboard(has_subscription=can_add)
        )
        await callback.answer()
    finally:
        await session.close()
