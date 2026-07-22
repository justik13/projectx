import asyncio
import logging
import math
import re
from urllib.parse import urlsplit, urlunsplit

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_admin_server_card_keyboard, get_back_button
from database.connection import session_scope
from database.models import PendingAPIDeletion
from database.repositories.servers_repo import (
    get_server_count,
    get_servers_paginated,
)
from services.amnezia_client import AmneziaClient
from utils.telegram import safe

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


async def _bulk_delete_peers_from_api(
    profiles_data,
    api_url: str,
    api_key: str,
) -> tuple[int, list[tuple[int, str]]]:
    if not profiles_data:
        return 0, []

    client = AmneziaClient(api_url, api_key)
    sem = asyncio.Semaphore(20)

    fail = 0
    failed_peers: list[tuple[int, str]] = []
    lock = asyncio.Lock()

    async def _delete_limited(profile_id: int, peer_id: str):
        nonlocal fail

        async with sem:
            ok = await client.delete_user(client_id=peer_id)

        async with lock:
            if not ok:
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

    return fail, failed_peers


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
        api_fail, failed_peers = await _bulk_delete_peers_from_api(
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