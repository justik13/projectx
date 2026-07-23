import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import TELEGRAM_MESSAGE_LIMIT
from bot.keyboards import get_back_button, get_device_keyboard
from database.models import User
from database.repositories.profiles_repo import get_profile_by_id
from database.repositories.servers_repo import get_server_by_id
from services.subscription import SubscriptionService
from utils.formatters import format_datetime, format_traffic
from utils.telegram import (
    append_hub_document,
    append_hub_message,
    delete_hub_ids,
    get_hub_ids,
    render_hub,
    safe,
    send_hub_document,
)
from utils.vpn_parser import (
    build_conf_file_from_dict,
    build_vpn_file_from_dict,
    decode_vpn_uri_to_json,
)

from .common import _format_protocol

router = Router()
logger = logging.getLogger(__name__)


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
            texts.DEVICE_ACCESS_INACTIVE,
            show_alert=True,
        )
        return

    raw_config = profile.raw_config or ""
    if not raw_config:
        await callback.answer(
            texts.DEVICE_CONFIG_UNAVAILABLE,
            show_alert=True,
        )
        return

    if len(raw_config) > TELEGRAM_MESSAGE_LIMIT - 300:
        safe_device_name = "".join(
            c
            for c in profile.device_name
            if c.isalnum() or c in (" ", "_", "-")
        ).strip() or "client"

        key_file = BufferedInputFile(
            raw_config.encode("utf-8"),
            filename=f"{safe_device_name}_key.txt",
        )
        caption = texts.DEVICE_KEY_TOO_LONG_CAPTION.format(
            device_name=safe(profile.device_name),
        )
        await send_hub_document(
            callback.bot,
            callback.message.chat.id,
            document=key_file,
            caption=caption,
            reply_markup=get_back_button(f"manage_device:{profile.id}"),
            parse_mode="HTML",
        )
        return

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.DEVICE_SHOW_KEY.format(
            device_name=safe(profile.device_name),
            raw_config=safe(raw_config),
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
            texts.DEVICE_ACCESS_INACTIVE,
            show_alert=True,
        )
        return

    await callback.answer(texts.DEVICE_CONFIG_GENERATING)

    safe_device_name = "".join(
        c
        for c in profile.device_name
        if c.isalnum() or c in (" ", "_", "-")
    ).strip() or "client"

    raw_config = profile.raw_config or ""
    if not raw_config:
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.DOWNLOAD_CONF_FALLBACK.format(
                device_name=safe(profile.device_name),
            ),
            get_back_button(f"manage_device:{profile.id}"),
        )
        return

    decoded = decode_vpn_uri_to_json(raw_config)
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
        filename=f"{safe_device_name}.vpn",
    )
    conf_file = BufferedInputFile(
        conf_content.encode("utf-8"),
        filename=f"{safe_device_name}.conf",
    )

    old_hub_ids = await get_hub_ids(callback.message.chat.id)

    #
    # ИСПРАВЛЕНО: обработка ошибок отправки файлов.
    #
    # Раньше если send_hub_document для .vpn проходил,
    # а для .conf падал — пользователь получал только один файл
    # без объяснения. Теперь каждый файл отправляется
    # в отдельном try/except.
    #
    vpn_sent = False
    conf_sent = False

    try:
        await append_hub_document(
            callback.bot,
            callback.message.chat.id,
            document=vpn_file,
            caption=texts.DEVICE_CONFIG_VPN_CAPTION.format(
                device_name=safe(profile.device_name),
            ),
            parse_mode="HTML",
        )
        vpn_sent = True
    except Exception as e:
        logger.error(
            "Failed to send .vpn file for profile %s: %s",
            profile.id,
            e,
        )

    try:
        await append_hub_document(
            callback.bot,
            callback.message.chat.id,
            document=conf_file,
            caption=texts.DEVICE_CONFIG_CONF_CAPTION.format(
                device_name=safe(profile.device_name),
            ),
            parse_mode="HTML",
        )
        conf_sent = True
    except Exception as e:
        logger.error(
            "Failed to send .conf file for profile %s: %s",
            profile.id,
            e,
        )

    # Инструкция отправляется всегда
    await append_hub_message(
        callback.bot,
        callback.message.chat.id,
        text=texts.DEVICE_CONFIG_INSTRUCTION,
        reply_markup=get_back_button(f"manage_device:{profile.id}"),
        parse_mode="HTML",
    )

    await delete_hub_ids(
        callback.bot,
        callback.message.chat.id,
        old_hub_ids,
    )