import asyncio
import logging
import math
import re
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import get_admin_server_card_keyboard, get_back_button
from bot.keyboards.admin.servers import get_server_delete_confirm_keyboard
from bot.states import AdminStates
from bot import texts
from database.models import VPNProfile
from database.repositories.servers_repo import (
    create_server, get_server_by_id, get_server_by_api_url,
    get_server_count, get_servers_paginated, update_server,
    delete_server, delete_profiles_by_server_id,
)
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import safe, render_hub
from utils.security import is_safe_url  # 🔥 ДОБАВЛЕНО

router = Router()
logger = logging.getLogger(__name__)
SERVERS_PER_PAGE = 10
URL_REGEX = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)

async def _build_servers_list_text_and_kb(
    servers, page: int, total_pages: int, total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        f"🛠 Админка › 🌍 <b>Серверы</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n"
    )
    builder = InlineKeyboardBuilder()
    if not servers:
        rendered += "<i>Серверов пока нет</i>\n"
    else:
        for server in servers:
            flag = server.country_flag or "🌍"
            status = "🟢" if server.is_active else "🔴"
            builder.button(
                text=f"{status} {flag} {safe(server.name)} · {server.protocol}",
                callback_data=f"admin_server_card:{server.id}",
            )
    if page > 1:
        builder.button(text="⬅️", callback_data=f"admin_servers_page:{page - 1}")
    if page < total_pages:
        builder.button(text="➡️", callback_data=f"admin_servers_page:{page + 1}")
    builder.button(text="➕ Добавить сервер", callback_data="admin_server_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder

async def _show_servers_list(callback: CallbackQuery, session: AsyncSession, page: int = 1):
    total_servers = await get_server_count(session)
    total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
    servers = await get_servers_paginated(session, page=page, per_page=SERVERS_PER_PAGE)
    rendered, kb = await _build_servers_list_text_and_kb(
        servers, page, total_pages, total_servers,
    )
    await callback.message.edit_text(rendered, reply_markup=kb.as_markup(), parse_mode="HTML")

async def _bulk_update_peer_status(
    profiles_data, api_url: str, api_key: str, status: str,
) -> bool:
    if not profiles_data:
        return True
    client = AmneziaClient(api_url, api_key)
    sem = asyncio.Semaphore(20)
    async def _limited(peer_id: str) -> bool:
        async with sem:
            return await client.update_client(client_id=peer_id, status=status)
    results = await asyncio.gather(
        *[_limited(pid) for _, pid in profiles_data],
        return_exceptions=True,
    )
    return not any(isinstance(r, Exception) or r is False for r in results)

async def _bulk_delete_peers_from_api(
    profiles_data, api_url: str, api_key: str,
) -> tuple[int, int]:
    if not profiles_data:
        return 0, 0
    client = AmneziaClient(api_url, api_key)
    sem = asyncio.Semaphore(20)
    success = 0
    fail = 0
    async def _delete_limited(peer_id: str):
        nonlocal success, fail
        async with sem:
            ok = await client.delete_client(client_id=peer_id)
            if ok:
                success += 1
            else:
                fail += 1
    await asyncio.gather(
        *[_delete_limited(pid) for _, pid in profiles_data],
        return_exceptions=True,
    )
    return success, fail

@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await _show_servers_list(callback, session, page=1)

@router.callback_query(F.data.startswith("admin_servers_page:"))
async def servers_pagination(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    await _show_servers_list(callback, session, page=page)

@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        texts.ADMIN_SERVER_NAME_PROMPT,
        reply_markup=get_back_button("admin_servers"),
    )
    await state.set_state(AdminStates.adding_server)
    await state.update_data(step="name")

@router.message(AdminStates.adding_server)
async def process_add_server(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers")
        )
        return
    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers")
        )
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "name":
        name = message.text.strip()
        if len(name) > 255:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_NAME_TOO_LONG.format(max=255),
                get_back_button("admin_servers")
            )
            return
        await state.update_data(name=name, step="flag")
        await render_hub(
            message.bot, message.chat.id,
            texts.ADMIN_SERVER_FLAG_PROMPT,
            get_back_button("admin_servers")
        )

    elif step == "flag":
        await state.update_data(country_flag=message.text.strip(), step="api_url")
        await render_hub(
            message.bot, message.chat.id,
            texts.ADMIN_SERVER_URL_PROMPT,
            get_back_button("admin_servers")
        )

    elif step == "api_url":
        api_url = message.text.strip()
        if len(api_url) > 500:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_URL_TOO_LONG.format(max=500),
                get_back_button("admin_servers")
            )
            return
        
        if not URL_REGEX.match(api_url):
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_INVALID_URL,
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            return

        # 🔥 ИСПРАВЛЕНО: Защита от SSRF
        if not await is_safe_url(api_url):
            await render_hub(
                message.bot, message.chat.id,
                "⚠️ <b>URL запрещен правилами безопасности</b>\n"
                "Использование приватных IP-адресов, loopback и metadata endpoints запрещено.",
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            return

        existing = await get_server_by_api_url(session, api_url)
        if existing:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(api_url=safe(api_url)),
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        await state.update_data(api_url=api_url, step="api_key")
        await render_hub(
            message.bot, message.chat.id,
            texts.ADMIN_SERVER_KEY_PROMPT,
            get_back_button("admin_servers")
        )

    elif step == "api_key":
        api_key = message.text.strip()
        if not api_key or len(api_key) < 8:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_API_KEY_SHORT.format(min=8),
                get_back_button("admin_servers")
            )
            return
        
        await state.update_data(api_key=api_key)
        all_data = await state.get_data()
        
        check_msg = await render_hub(
            message.bot, message.chat.id,
            texts.ADMIN_SERVER_CHECKING,
            get_back_button("admin_servers"),
            parse_mode="HTML"
        )
        
        client = AmneziaClient(all_data["api_url"], all_data["api_key"])
        if not await client.healthcheck():
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_SERVER_UNREACHABLE,
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            await state.clear()
            return

        server_info = await client.get_server_info()
        if not server_info:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_SERVER_API_INFO_FAILED,
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            await state.clear()
            return

        protocols = server_info.get("protocols", [])
        if "amneziawg2" not in protocols:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_PROTOCOL_NOT_SUPPORTED.format(
                    protocols=safe(", ".join(protocols) if protocols else "неизвестно"),
                ),
                get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            await state.clear()
            return

        api_max_peers = (
            server_info.get("maxPeers")
            or server_info.get("serverMaxPeers")
            or server_info.get("SERVER_MAX_PEERS", 250)
        )
        api_server_name = (
            server_info.get("name")
            or server_info.get("serverName")
            or all_data["name"]
        )

        existing = await get_server_by_api_url(session, all_data["api_url"])
        if existing:
            await render_hub(
                message.bot, message.chat.id,
                texts.ERROR_SERVER_DUPLICATE_URL.format(api_url=safe(all_data["api_url"])),
                get_back_button("admin_servers"),
                parse_mode="HTML"
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
            max_clients=int(api_max_peers),
        )
        await AuditService.log_action(
            session, message.from_user.id, "ADD_SERVER", "Server", server.id, api_server_name,
        )
        await render_hub(
            message.bot, message.chat.id,
            texts.ADMIN_SERVER_ADDED.format(
                flag=all_data["country_flag"],
                name=safe(api_server_name),
                protocol="amneziawg2",
                max_clients=api_max_peers,
                api_url=safe(all_data["api_url"]),
            ),
            get_back_button("admin_servers"),
            parse_mode="HTML"
        )
        logger.info(f"Admin {message.from_user.id} added server: {server.id}")
        await state.clear()

async def _show_server_card(callback: CallbackQuery, session: AsyncSession, server):
    flag = server.country_flag or "🌍"
    status = "🟢 Активен" if server.is_active else "🔴 Отключен"
    rendered = texts.ADMIN_SERVER_CARD.format(
        flag=flag,
        name=safe(server.name),
        id=server.id,
        status=status,
        protocol=server.protocol,
        api_url=server.api_url,
        max_clients=server.max_clients,
    )
    await callback.message.edit_text(
        rendered,
        reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return
    await _show_server_card(callback, session, server)

@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await callback.answer("⏳ Выполняется...")
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return
    new_status = not server.is_active
    result = await session.execute(
        select(VPNProfile.id, VPNProfile.peer_id).where(VPNProfile.server_id == server.id),
    )
    profiles_data = result.all()
    if profiles_data:
        target_api_status = "active" if new_status else "disabled"
        ok = await _bulk_update_peer_status(
            profiles_data, server.api_url, server.api_key, target_api_status,
        )
        if not ok:
            await callback.answer(texts.ADMIN_TOGGLE_NETWORK_FAIL, show_alert=True)
            return
        profile_ids = [pid for pid, _ in profiles_data]
        await session.execute(
            update(VPNProfile)
            .where(VPNProfile.id.in_(profile_ids))
            .values(is_active=new_status),
        )
    await update_server(session, server, is_active=new_status)
    await AuditService.log_action(
        session, callback.from_user.id, "TOGGLE_SERVER", "Server", server_id,
        "enabled" if new_status else "disabled",
    )
    await session.commit()
    await callback.answer(
        f"✅ Сервер {'включен' if new_status else 'выключен'}", show_alert=True,
    )
    logger.info(f"Admin {callback.from_user.id} toggled server {server_id} to {new_status}")
    refreshed = await get_server_by_id(session, server_id)
    await _show_server_card(callback, session, refreshed)

@router.callback_query(F.data.startswith("admin_server_delete:"))
async def request_delete_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return
    result = await session.execute(
        select(VPNProfile.id).where(VPNProfile.server_id == server.id),
    )
    profiles_count = len(result.all())
    flag = server.country_flag or "🌍"
    await state.update_data(delete_server_id=server_id)
    await state.set_state(AdminStates.confirming_server_delete)
    await callback.message.edit_text(
        texts.ADMIN_SERVER_DELETE_CONFIRM.format(
            flag=flag,
            name=safe(server.name),
            profiles_count=profiles_count,
        ),
        reply_markup=get_server_delete_confirm_keyboard(server_id),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("confirm_server_delete:"))
async def confirm_delete_server(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    current_state = await state.get_state()
    if current_state != AdminStates.confirming_server_delete:
        await callback.answer("⚠️ Сессия подтверждения истекла", show_alert=True)
        return
    await state.clear()
    await callback.answer("⏳ Удаляю сервер и все устройства...", show_alert=False)
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        await _show_servers_list(callback, session, page=1)
        return
    flag = server.country_flag or "🌍"
    server_name = server.name
    result = await session.execute(
        select(VPNProfile.id, VPNProfile.peer_id).where(VPNProfile.server_id == server.id),
    )
    profiles_data = result.all()
    profiles_count = len(profiles_data)
    if profiles_data:
        api_success, api_fail = await _bulk_delete_peers_from_api(
            profiles_data, server.api_url, server.api_key,
        )
        if api_fail > 0:
            logger.warning(
                f"Server {server_id}: {api_fail}/{profiles_count} peers "
                f"failed to delete from API"
            )
    deleted_profiles = await delete_profiles_by_server_id(session, server_id)
    await delete_server(session, server)
    await AuditService.log_action(
        session, callback.from_user.id, "DELETE_SERVER", "Server", server_id,
        f"{server_name}: {deleted_profiles} profiles deleted",
    )
    await callback.answer(
        f"✅ Сервер {server_name} удалён ({deleted_profiles} устр.)",
        show_alert=True,
    )
    logger.info(
        f"Admin {callback.from_user.id} fully deleted server {server_id} "
        f"({server_name}) with {deleted_profiles} profiles"
    )
    await _show_servers_list(callback, session, page=1)

@router.callback_query(F.data.startswith("admin_server_edit_name:"))
async def start_edit_server_name(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    server_id = int(callback.data.split(":")[1])
    await state.update_data(server_id=server_id, edit_field="name")
    await state.set_state(AdminStates.editing_server)
    await callback.message.edit_text(
        texts.ADMIN_SERVER_RENAME_PROMPT,
        reply_markup=get_back_button(f"admin_server_card:{server_id}"),
    )

@router.callback_query(F.data.startswith("admin_server_edit_flag:"))
async def start_edit_server_flag(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    server_id = int(callback.data.split(":")[1])
    server = await get_server_by_id(session, server_id)
    if not server:
        await callback.answer(texts.ERROR_SERVER_NOT_FOUND, show_alert=True)
        return
    current_flag = server.country_flag or "🌍"
    await state.update_data(server_id=server_id, edit_field="flag")
    await state.set_state(AdminStates.editing_server_flag)
    await callback.message.edit_text(
        texts.ADMIN_SERVER_FLAG_PROMPT_EDIT.format(current_flag=current_flag),
        reply_markup=get_back_button(f"admin_server_card:{server_id}"),
    )

@router.message(AdminStates.editing_server_flag)
async def process_edit_server_flag(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers")
        )
        return
    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers")
        )
        return
    data = await state.get_data()
    server_id = data["server_id"]
    server = await get_server_by_id(session, server_id)
    if not server:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers")
        )
        await state.clear()
        return
    new_flag = message.text.strip()
    if len(new_flag) > 10:
        await render_hub(
            message.bot, message.chat.id,
            "⚠️ Флаг слишком длинный (макс. 10 символов):",
            get_back_button("admin_servers")
        )
        return
    await update_server(session, server, country_flag=new_flag)
    await render_hub(
        message.bot, message.chat.id,
        texts.ADMIN_SERVER_FLAG_UPDATED.format(flag=new_flag),
        get_back_button(f"admin_server_card:{server_id}")
    )
    logger.info(f"Admin {message.from_user.id} updated server {server_id} flag to {new_flag}")
    await state.clear()

@router.message(AdminStates.editing_server)
async def process_edit_server_name(message: Message, state: FSMContext, session: AsyncSession):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_TEXT_REQUIRED,
            get_back_button("admin_servers")
        )
        return
    if message.text.startswith("/"):
        await state.clear()
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_OPERATION_CANCELLED,
            get_back_button("admin_servers")
        )
        return
    data = await state.get_data()
    server_id = data["server_id"]
    server = await get_server_by_id(session, server_id)
    if not server:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers")
        )
        await state.clear()
        return
    new_name = message.text.strip()
    if len(new_name) > 255:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_NAME_TOO_LONG.format(max=255),
            get_back_button("admin_servers")
        )
        return
    await update_server(session, server, name=new_name)
    await render_hub(
        message.bot, message.chat.id,
        texts.ADMIN_SERVER_RENAMED.format(name=safe(new_name)),
        get_back_button(f"admin_server_card:{server_id}")
    )
    logger.info(f"Admin {message.from_user.id} updated server {server_id} name to {new_name}")
    await state.clear()