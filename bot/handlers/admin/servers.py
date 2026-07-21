import asyncio
import logging
import math
import re

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlsplit, urlunsplit
from bot import texts
from bot.keyboards import get_admin_server_card_keyboard, get_back_button
from bot.keyboards.admin.servers import get_server_delete_confirm_keyboard
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from bot.states import AdminStates
from database.connection import session_scope, queue_post_commit_task
from database.models import PendingAPIDeletion, VPNProfile
from database.repositories.servers_repo import (
    create_server,
    delete_profiles_by_server_id,
    delete_server,
    get_server_by_api_url,
    get_server_by_id,
    get_server_count,
    get_servers_paginated,
    update_server,
)
from services.amnezia_client import AmneziaClient, cleanup_server_circuit_breakers
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.security import is_safe_url
from utils.telegram import render_hub, safe

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

def normalize_api_url(url: str) -> str:
    """
    Нормализует API URL:

    - убирает trailing slash;
    - приводит scheme и host к нижнему регистру;
    - сохраняет порт и путь.

    Пример:

        HTTP://Example.com:4001/  ->  http://example.com:4001
    """

    url = url.strip()

    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")

    return urlunsplit(
        (
            scheme,
            netloc,
            path,
            parts.query,
            parts.fragment,
        )
    )

async def _build_servers_list_text_and_kb(
    servers,
    page: int,
    total_pages: int,
    total: int,
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
            builder.button(
                text="⬅️",
                callback_data=f"admin_servers_page:{page - 1}",
            )

        if page < total_pages:
            builder.button(
                text="➡️",
                callback_data=f"admin_servers_page:{page + 1}",
            )

        builder.button(
            text="➕ Добавить сервер",
            callback_data="admin_server_add",
        )

        builder.button(
            text="← В админку",
            callback_data="admin_menu",
        )

        builder.adjust(1)

    return rendered, builder


async def _show_servers_list(
    callback: CallbackQuery,
    session: AsyncSession,
    page: int = 1,
):
    total_servers = await get_server_count(session)

    total_pages = max(
        1,
        math.ceil(total_servers / SERVERS_PER_PAGE),
    )

    servers = await get_servers_paginated(
        session,
        page=page,
        per_page=SERVERS_PER_PAGE,
    )

    rendered, kb = await _build_servers_list_text_and_kb(
        servers,
        page,
        total_pages,
        total_servers,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=kb.as_markup(),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"_show_servers_list edit_text failed: {e}")


async def _bulk_delete_peers_from_api(
    profiles_data,
    api_url: str,
    api_key: str,
) -> tuple[int, int, list[tuple[int, str]]]:
    if not profiles_data:
        return 0, 0, []

    client = AmneziaClient(api_url, api_key)

    sem = asyncio.Semaphore(20)

    success = 0
    fail = 0
    failed_peers: list[tuple[int, str]] = []

    lock = asyncio.Lock()

    async def _delete_limited(profile_id: int, peer_id: str):
        nonlocal success, fail

        async with sem:
            ok = await client.delete_user(client_id=peer_id)

            async with lock:
                if ok:
                    success += 1
                else:
                    fail += 1
                    failed_peers.append((profile_id, peer_id))

    try:
        await asyncio.wait_for(
            asyncio.gather(
                *[
                    _delete_limited(pid, peer)
                    for pid, peer in profiles_data
                ],
                return_exceptions=True,
            ),
            timeout=300.0,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"_bulk_delete_peers_from_api: timeout after 300s "
            f"for {len(profiles_data)} peers"
        )

    return success, fail, failed_peers


async def _delete_server_background(
    bot,
    admin_id: int,
    server_name: str,
    profiles_data: list,
    api_url: str,
    api_key: str,
    deleted_profiles: int,
):
    if profiles_data:
        api_success, api_fail, failed_peers = await _bulk_delete_peers_from_api(
            profiles_data,
            api_url,
            api_key,
        )

        if failed_peers:
            try:
                async with session_scope() as session:
                    for profile_id, peer_id in failed_peers:
                        pending = PendingAPIDeletion(
                            server_name=server_name,
                            api_url=api_url,
                            api_key=api_key,
                            peer_id=peer_id,
                            client_name=f"tg_*_{profile_id}",
                            attempts=1,
                            reason="server_delete_api_failed",
                        )

                        session.add(pending)

                    await session.commit()

                logger.info(
                    f"Saved {len(failed_peers)} zombie peers to pending_api_deletions"
                )

            except Exception as e:
                logger.error(f"Failed to save zombie peers: {e}")

        msg = (
            f"⚠️ Сервер {server_name} удалён из БД ({deleted_profiles} устр.),\n"
            f"но {api_fail}/{len(profiles_data)} пиров не удалось удалить из API.\n"
            f"Worker Cleanup подчистит позже."
        )
    else:
        msg = f"✅ Сервер {server_name} удалён"

    try:
        await bot.send_message(admin_id, msg)
    except Exception as e:
        logger.error(f"Failed to send background message: {e}")


@router.callback_query(F.data == "admin_servers")
async def show_servers_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    await _show_servers_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_servers_page:"))
async def servers_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    page = int(callback.data.split(":")[1])

    await _show_servers_list(callback, session, page=page)


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


async def _show_server_card(
    callback: CallbackQuery,
    session: AsyncSession,
    server,
):
    flag = server.country_flag or "🌍"

    status = (
        "🟢 Активен"
        if server.is_active
        else "🔴 Отключен"
    )

    rendered = texts.ADMIN_SERVER_CARD.format(
        flag=flag,
        name=safe(server.name),
        id=server.id,
        status=status,
        protocol=server.protocol,
        api_url=safe(server.api_url),
        max_clients=server.max_clients,
    )

    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_server_card_keyboard(
                server.id,
                server.is_active,
            ),
            parse_mode="HTML",
        )

    except TelegramBadRequest as e:
        logger.debug(f"_show_server_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _show_server_card(callback, session, server)


@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active

    flag = server.country_flag or "🌍"

    if new_status:
        text = (
            "⚠️ <b>Подтверждение включения сервера</b>\n"
            f"{flag} <b>{safe(server.name)}</b>\n\n"
            "Сервер снова будет доступен пользователям\n"
            "при создании новых устройств.\n\n"
            "<i>Существующие устройства продолжат работать.</i>"
        )
    else:
        text = (
            "⚠️ <b>Подтверждение отключения сервера</b>\n"
            f"{flag} <b>{safe(server.name)}</b>\n\n"
            "Сервер будет скрыт из списка доступных локаций\n"
            "при создании новых устройств.\n\n"
            "<i>Существующие устройства продолжат работать.</i>"
        )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_server_toggle_apply:{server_id}",
                cancel_callback=f"admin_server_card:{server_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"toggle_server_confirm edit_text failed: {e}")

    await callback.answer()


@router.callback_query(F.data.startswith("admin_server_toggle_apply:"))
async def toggle_server_apply(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    new_status = not server.is_active

    await update_server(
        session,
        server,
        is_active=new_status,
    )

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "TOGGLE_SERVER",
        "Server",
        server_id,
        "enabled" if new_status else "disabled",
    )

    status_text = (
        "включен"
        if new_status
        else "выключен"
    )

    await callback.answer(
        f"✅ Сервер {status_text}",
        show_alert=True,
    )

    logger.info(
        f"Admin {callback.from_user.id} toggled server {server_id} "
        f"to {new_status}"
    )

    refreshed = await get_server_by_id(session, server_id)

    await _show_server_card(callback, session, refreshed)


@router.callback_query(F.data.startswith("admin_server_delete:"))
async def request_delete_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )
        return

    result = await session.execute(
        select(VPNProfile.id).where(
            VPNProfile.server_id == server.id
        ),
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
async def confirm_delete_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    current_state = await state.get_state()

    if current_state != AdminStates.confirming_server_delete:
        await callback.answer(
            "⚠️ Сессия подтверждения истекла",
            show_alert=True,
        )
        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )

        await _show_servers_list(callback, session, page=1)

        return

    server_name = server.name
    api_url = server.api_url
    api_key = server.api_key

    result = await session.execute(
        select(
            VPNProfile.id,
            VPNProfile.peer_id,
        ).where(
            VPNProfile.server_id == server.id
        ),
    )

    profiles_data = result.all()

    deleted_profiles = await delete_profiles_by_server_id(
        session,
        server_id,
    )

    await delete_server(session, server)

    cleanup_server_circuit_breakers(api_url)

    await AuditService.log_action(
        session,
        callback.from_user.id,
        "DELETE_SERVER",
        "Server",
        server_id,
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

    if profiles_data:
        queue_post_commit_task(
            session,
            lambda bot=callback.bot,
                   admin_id=callback.from_user.id,
                   srv_name=server_name,
                   data=profiles_data,
                   url=api_url,
                   key=api_key,
                   deleted=deleted_profiles: (
                _delete_server_background(
                    bot,
                    admin_id,
                    srv_name,
                    data,
                    url,
                    key,
                    deleted,
                )
            ),
        )


@router.callback_query(F.data.startswith("admin_server_edit_name:"))
async def start_edit_server_name(
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

    server_id = int(callback.data.split(":")[1])

    await state.update_data(
        server_id=server_id,
        edit_field="name",
    )

    await state.set_state(AdminStates.editing_server)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_RENAME_PROMPT,
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.callback_query(F.data.startswith("admin_server_edit_flag:"))
async def start_edit_server_flag(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )

        return

    await state.clear()

    server_id = int(callback.data.split(":")[1])

    server = await get_server_by_id(session, server_id)

    if not server:
        await callback.answer(
            texts.ERROR_SERVER_NOT_FOUND,
            show_alert=True,
        )

        return

    current_flag = server.country_flag or "🌍"

    await state.update_data(
        server_id=server_id,
        edit_field="flag",
    )

    await state.set_state(AdminStates.editing_server_flag)

    await callback.message.edit_text(
        texts.ADMIN_SERVER_FLAG_PROMPT_EDIT.format(
            current_flag=safe(current_flag),
        ),
        reply_markup=get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )


@router.message(AdminStates.editing_server_flag)
async def process_edit_server_flag(
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

    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_flag = message.text.strip()

    if len(new_flag) > 10:
        await render_hub(
            message.bot,
            message.chat.id,
            "⚠️ Флаг слишком длинный (макс. 10 символов):",
            get_back_button("admin_servers"),
        )

        return

    await update_server(
        session,
        server,
        country_flag=new_flag,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"flag -> {new_flag}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_FLAG_UPDATED.format(flag=safe(new_flag)),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"flag to {new_flag}"
    )

    await state.clear()


@router.message(AdminStates.editing_server)
async def process_edit_server_name(
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

    server_id = data["server_id"]

    server = await get_server_by_id(session, server_id)

    if not server:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_SERVER_NOT_FOUND,
            get_back_button("admin_servers"),
        )

        await state.clear()

        return

    new_name = message.text.strip()

    if len(new_name) > 255:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_NAME_TOO_LONG.format(max=255),
            get_back_button("admin_servers"),
        )

        return

    await update_server(
        session,
        server,
        name=new_name,
    )

    await AuditService.log_action(
        session,
        message.from_user.id,
        "EDIT_SERVER",
        "Server",
        server_id,
        f"name -> {new_name}",
    )

    await render_hub(
        message.bot,
        message.chat.id,
        texts.ADMIN_SERVER_RENAMED.format(
            name=safe(new_name),
        ),
        get_back_button(
            f"admin_server_card:{server_id}"
        ),
    )

    logger.info(
        f"Admin {message.from_user.id} updated server {server_id} "
        f"name to {new_name}"
    )

    await state.clear()