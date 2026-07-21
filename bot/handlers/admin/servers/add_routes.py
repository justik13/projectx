import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.states import AdminStates
from database.repositories.servers_repo import (
    create_server,
    get_server_by_api_url,
)
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.security import is_safe_url
from utils.telegram import render_hub, safe

from .common import URL_REGEX, normalize_api_url

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    await callback.message.edit_text(
        texts.ADMIN_SERVER_NAME_PROMPT,
        reply_markup=get_back_button("admin_servers"),
    )

    await state.set_state(AdminStates.adding_server)
    await state.update_data(step="name")


@router.message(AdminStates.adding_server)
async def process_add_server(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if not message.text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers"),
        )
        return

    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers"),
        )
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "name":
        name = message.text.strip()

        if len(name) > 255:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_NAME_TOO_LONG.format(max=255),
                get_back_button("admin_servers"),
            )
            return

        await state.update_data(name=name, step="flag")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_FLAG_PROMPT,
            get_back_button("admin_servers"),
        )

    elif step == "flag":
        country_flag = message.text.strip()

        if len(country_flag) > 10:
            await render_hub(
                message.bot,
                message.chat.id,
                "⚠️ Флаг слишком длинный (макс. 10 символов).",
                get_back_button("admin_servers"),
            )
            return

        await state.update_data(
            country_flag=country_flag,
            step="api_url",
        )

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_URL_PROMPT,
            get_back_button("admin_servers"),
        )

    elif step == "api_url":
        api_url = normalize_api_url(message.text)

        if len(api_url) > 500:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_URL_TOO_LONG.format(max=500),
                get_back_button("admin_servers"),
            )
            return

        if not URL_REGEX.match(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_INVALID_URL,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            return

        if not await is_safe_url(api_url):
            await render_hub(
                message.bot,
                message.chat.id,
                "⚠️ <b>URL запрещён правилами безопасности</b>\n"
                "Использование приватных IP-адресов, loopback и "
                "metadata endpoints запрещено.",
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            return

        existing = await get_server_by_api_url(session, api_url)
        if existing:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(
                    api_url=safe(api_url),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        await state.update_data(api_url=api_url, step="api_key")

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_KEY_PROMPT,
            get_back_button("admin_servers"),
        )

    elif step == "api_key":
        api_key = message.text.strip()

        if not api_key or len(api_key) < 8:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_API_KEY_SHORT.format(min=8),
                get_back_button("admin_servers"),
            )
            return

        await state.update_data(api_key=api_key)

        all_data = await state.get_data()

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_CHECKING,
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        client = AmneziaClient(
            all_data["api_url"],
            all_data["api_key"],
        )

        if not await client.healthcheck():
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_UNREACHABLE,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        server_info = await client.get_server_info()
        if not server_info:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_API_INFO_FAILED,
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        protocols = server_info.protocols

        if "amneziawg2" not in protocols:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_PROTOCOL_NOT_SUPPORTED.format(
                    protocols=safe(
                        ", ".join(protocols) if protocols else "неизвестно"
                    ),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        api_max_peers = server_info.get_effective_max_peers()
        api_server_name = server_info.name or all_data["name"]

        existing = await get_server_by_api_url(
            session,
            all_data["api_url"],
        )

        if existing:
            await render_hub(
                message.bot,
                message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(
                    api_url=safe(all_data["api_url"]),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML",
            )
            await state.clear()
            return

        server = await create_server(
            session,
            name=api_server_name,
            country_flag=all_data["country_flag"],
            api_url=all_data["api_url"],
            api_key=all_data["api_key"],
            protocol="amneziawg2",
            max_clients=api_max_peers,
        )

        await AuditService.log_action(
            session,
            message.from_user.id,
            "ADD_SERVER",
            "Server",
            server.id,
            api_server_name,
        )

        await render_hub(
            message.bot,
            message.chat.id,
            texts.ADMIN_SERVER_ADDED.format(
                flag=all_data["country_flag"],
                name=safe(api_server_name),
                protocol="amneziawg2",
                max_clients=api_max_peers,
                api_url=safe(all_data["api_url"]),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML",
        )

        logger.info(
            f"Admin {message.from_user.id} added server: {server.id}"
        )

        await state.clear()