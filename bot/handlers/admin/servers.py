import html
import logging
import asyncio
import re
import math
from urllib.parse import urlparse
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.connection import get_session
from database.repositories.servers_repo import (
    get_all_servers, get_server_by_id, create_server, update_server, delete_server,
    get_server_count, get_servers_paginated
)
from bot.keyboards import get_admin_servers_keyboard, get_admin_server_card_keyboard, get_back_button
from bot.states import AdminStates
from config.settings import get_settings
from sqlalchemy import select, update
from database.models import VPNProfile
from services.amnezia_client import AmneziaClient
from services.audit_service import AuditService

router = Router()
logger = logging.getLogger(__name__)

SERVERS_PER_PAGE = 10


def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS


REPLY_MENU_BUTTONS = ["👤 Профиль", "🔌 Подключение", "💳 Оплата", "💬 Поддержка", "🛠 Админка"]

URL_REGEX = re.compile(
    r'^https?://'
    r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'
    r'localhost|'
    r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
    r'(?::\d+)?'
    r'(?:/?|[/?]\S+)$', re.IGNORECASE
)


async def _build_servers_list_text_and_kb(servers, page: int, total_pages: int, total: int) -> tuple[str, InlineKeyboardBuilder]:
    """🔥 НОВОЕ: сборка текста и клавиатуры с пагинацией"""
    text = (
        f"🛠 Админка › 🌍 <b>Серверы</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n\n"
    )
    builder = InlineKeyboardBuilder()

    if not servers:
        text += "_Серверов пока нет_\n"
    else:
        for server in servers:
            flag = server.country_flag or "🌍"
            status = "🟢" if server.is_active else "🔴"
            safe_name = html.escape(server.name)
            btn_text = f"{status} {flag} {safe_name} · {server.protocol}"
            builder.button(text=btn_text, callback_data=f"admin_server_card:{server.id}")

    nav_buttons = []
    if page > 1:
        nav_buttons.append(("⬅️", f"admin_servers_page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(("➡️", f"admin_servers_page:{page + 1}"))
    for btn_text, btn_data in nav_buttons:
        builder.button(text=btn_text, callback_data=btn_data)

    builder.button(text="➕ Добавить сервер", callback_data="admin_server_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return text, builder


@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    session = await get_session()
    try:
        total_servers = await get_server_count(session)
        total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
        servers = await get_servers_paginated(session, page=1, per_page=SERVERS_PER_PAGE)
        text, kb = await _build_servers_list_text_and_kb(servers, 1, total_pages, total_servers)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_servers_page:"))
async def servers_pagination(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        total_servers = await get_server_count(session)
        total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
        servers = await get_servers_paginated(session, page=page, per_page=SERVERS_PER_PAGE)
        text, kb = await _build_servers_list_text_and_kb(servers, page, total_pages, total_servers)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 Админка › 🌍 Серверы › ➕ <b>Новый сервер</b>\n\n"
        "✏️ Введите имя сервера (например: Нидерланды):",
        reply_markup=get_back_button("admin_servers")
    )
    await state.set_state(AdminStates.adding_server)
    await state.update_data(step="name")
    await callback.answer()


@router.message(AdminStates.adding_server)
async def process_add_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("admin_servers"))
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "name":
        if len(message.text.strip()) > 255:
            await message.answer("⚠️ Слишком длинное имя (макс. 255 символов).")
            return
        await state.update_data(name=message.text.strip(), step="flag")
        await message.answer(
            "🏳️ Введите флаг страны (эмодзи, например: 🇳🇱):",
            reply_markup=get_back_button("admin_servers")
        )
    elif step == "flag":
        await state.update_data(country_flag=message.text.strip(), step="api_url")
        await message.answer(
            "🔗 Введите API URL сервера (например: http://127.0.0.1:4001):",
            reply_markup=get_back_button("admin_servers")
        )
    elif step == "api_url":
        api_url = message.text.strip()
        if len(api_url) > 500:
            await message.answer("⚠️ Слишком длинный URL (макс. 500 символов).")
            return
        if not URL_REGEX.match(api_url):
            await message.answer(
                "⚠️ Некорректный формат URL.\n"
                "URL должен начинаться с <code>http://</code> или <code>https://</code>\n"
                "Пример: <code>http://127.0.0.1:4001</code>",
                parse_mode="HTML"
            )
            return
        await state.update_data(api_url=api_url, step="api_key")
        await message.answer(
            "🔑 Введите API ключ сервера:",
            reply_markup=get_back_button("admin_servers")
        )
    elif step == "api_key":
        api_key = message.text.strip()
        if not api_key or len(api_key) < 8:
            await message.answer("⚠️ API ключ слишком короткий (минимум 8 символов).")
            return
        await state.update_data(api_key=api_key, step="check")

        all_data = await state.get_data()
        check_msg = await message.answer(
            "🔍 <b>Проверяю доступность сервера...</b>\n"
            "Ожидайте, это может занять несколько секунд.",
            parse_mode="HTML"
        )

        client = AmneziaClient(all_data["api_url"], all_data["api_key"])
        is_healthy = await client.healthcheck()
        if not is_healthy:
            await check_msg.edit_text(
                "❌ <b>Сервер недоступен!</b>\n"
                "Не удалось подключиться к Amnezia API по указанному адресу.\n"
                "Возможные причины:\n"
                "• Неверный URL или API ключ\n"
                "• Сервер выключен или недоступен\n"
                "• Файрвол блокирует соединение\n"
                "• Amnezia API не запущен\n\n"
                "Проверьте данные и попробуйте снова.",
                parse_mode="HTML"
            )
            await state.clear()
            return

        server_info = await client.get_server_info()
        if not server_info:
            await check_msg.edit_text(
                "❌ <b>Ошибка подключения к API!</b>\n"
                "Сервер отвечает на healthcheck, но не удалось получить информацию.\n"
                "Возможно, неверный API ключ.",
                parse_mode="HTML"
            )
            await state.clear()
            return

        protocols = server_info.get("protocols", [])
        if "amneziawg2" not in protocols:
            available = ", ".join(protocols) if protocols else "неизвестно"
            await check_msg.edit_text(
                f"⚠️ <b>Протокол amneziawg2 не поддерживается!</b>\n"
                f"Доступные протоколы на сервере: <code>{html.escape(available)}</code>\n"
                f"Этот бот работает только с протоколом <b>amneziawg2</b>.",
                parse_mode="HTML"
            )
            await state.clear()
            return

        # 🔥 ИСПРАВЛЕНО: Парсим лимит и имя из API, а не спрашиваем у админа
        api_max_peers = server_info.get("maxPeers") or server_info.get("serverMaxPeers") or server_info.get("SERVER_MAX_PEERS", 250)
        api_server_name = server_info.get("name") or server_info.get("serverName") or all_data["name"]

        session = await get_session()
        try:
            server = await create_server(
                session, name=api_server_name, country_flag=all_data["country_flag"],
                api_url=all_data["api_url"], api_key=all_data["api_key"],
                protocol="amneziawg2", max_clients=int(api_max_peers)
            )
            await AuditService.log_action(
                session, message.from_user.id, "ADD_SERVER", "Server", server.id, api_server_name
            )

            safe_name = html.escape(api_server_name)
            await check_msg.edit_text(
                f"✅ <b>Сервер добавлен и проверен!</b>\n\n"
                f"{all_data['country_flag']} <b>{safe_name}</b>\n"
                f"Протокол: amneziawg2\n"
                f"Макс клиентов (из API): {api_max_peers}\n"
                f"API: <code>{html.escape(all_data['api_url'])}</code>",
                reply_markup=get_back_button("admin_servers"), parse_mode="HTML"
            )
            logger.info(f"Admin {message.from_user.id} added server: {server.id}")
            await state.clear()
        finally:
            await session.close()


@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return

        flag = server.country_flag or "🌍"
        status = "🟢 Активен" if server.is_active else "🔴 Отключен"
        safe_name = html.escape(server.name)
        text = (
            f"🛠 Админка › 🌍 Серверы › {flag} <b>{safe_name}</b>\n\n"
            f"<b>ID:</b> {server.id}\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Протокол:</b> {server.protocol}\n"
            f"<b>API URL:</b> {server.api_url}\n"
            f"<b>Макс клиентов:</b> {server.max_clients}\n"
        )
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("⏳ Выполняется...")
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        new_status = not server.is_active
        server_info = {'api_url': server.api_url, 'api_key': server.api_key}
        result = await session.execute(
            select(VPNProfile.id, VPNProfile.peer_id).where(VPNProfile.server_id == server.id)
        )
        profiles_data = result.all()
    finally:
        await session.close()

    network_success = True
    if profiles_data:
        client = AmneziaClient(server_info['api_url'], server_info['api_key'])
        target_status = "active" if new_status else "disabled"
        sem = asyncio.Semaphore(20)
        async def _limited_update(peer_id, status):
            async with sem:
                return await client.update_client(client_id=peer_id, status=status)
        tasks = [_limited_update(peer_id, target_status) for _, peer_id in profiles_data]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        api_errors = [r for r in results if isinstance(r, Exception) or r is False]
        if api_errors:
            network_success = False

    if not network_success and profiles_data:
        await callback.answer("⚠️ Amnezia API недоступен. Статус сервера не изменён.", show_alert=True)
        return

    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        await update_server(session, server, is_active=new_status)
        await AuditService.log_action(
            session, callback.from_user.id, "TOGGLE_SERVER", "Server", server_id,
            "enabled" if new_status else "disabled"
        )
        if profiles_data:
            profile_ids = [p_id for p_id, _ in profiles_data]
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids))
                .values(is_active=new_status)
            )
            await session.commit()

        action = "включен" if new_status else "выключен"
        await callback.answer(f"✅ Сервер {action}", show_alert=True)
        logger.info(f"Admin {callback.from_user.id} toggled server {server_id} to {new_status}")

        server = await get_server_by_id(session, server_id)
        flag = server.country_flag or "🌍"
        status = "🟢 Активен" if server.is_active else "🔴 Отключен"
        safe_name = html.escape(server.name)
        text = (
            f"🛠 Админка › 🌍 Серверы › {flag} <b>{safe_name}</b>\n\n"
            f"<b>ID:</b> {server.id}\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Протокол:</b> {server.protocol}\n"
            f"<b>API URL:</b> {server.api_url}\n"
            f"<b>Макс клиентов:</b> {server.max_clients}\n"
        )
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_delete:"))
async def delete_server_handler(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.answer("⏳ Выполняется параллельное отключение пиров...")
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        result = await session.execute(
            select(VPNProfile.id, VPNProfile.peer_id).where(VPNProfile.server_id == server.id)
        )
        profiles_data = result.all()
        server_info = {'api_url': server.api_url, 'api_key': server.api_key}
    finally:
        await session.close()

    network_success = True
    if profiles_data:
        client = AmneziaClient(server_info['api_url'], server_info['api_key'])
        sem = asyncio.Semaphore(20)
        async def _limited_disable(peer_id):
            async with sem:
                return await client.update_client(client_id=peer_id, status="disabled")
        tasks = [_limited_disable(peer_id) for _, peer_id in profiles_data]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        api_errors = [r for r in results if isinstance(r, Exception) or r is False]
        if api_errors:
            network_success = False

    session = await get_session()
    try:
        if network_success:
            if profiles_data:
                profile_ids = [p_id for p_id, _ in profiles_data]
                await session.execute(
                    update(VPNProfile)
                    .where(VPNProfile.id.in_(profile_ids))
                    .values(is_active=False)
                )
            server = await get_server_by_id(session, server_id)
            await update_server(session, server, is_active=False)
            await AuditService.log_action(
                session, callback.from_user.id, "DELETE_SERVER", "Server", server_id, server.name
            )
            await session.commit()
            await callback.answer("✅ Сервер и связанные устройства успешно отключены", show_alert=True)
            logger.info(f"Admin {callback.from_user.id} disabled server {server_id}")
        else:
            await callback.answer(
                "⚠️ Ошибка сети: не удалось отключить устройства на сервере. БД не изменена.",
                show_alert=True
            )
            return

        # Возврат к списку с пагинацией
        total_servers = await get_server_count(session)
        total_pages = max(1, math.ceil(total_servers / SERVERS_PER_PAGE))
        servers = await get_servers_paginated(session, page=1, per_page=SERVERS_PER_PAGE)
        text, kb = await _build_servers_list_text_and_kb(servers, 1, total_pages, total_servers)
        await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_edit:"))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    server_id = int(callback.data.split(":")[1])
    await state.update_data(server_id=server_id)
    await state.set_state(AdminStates.editing_server)
    await callback.message.edit_text(
        "🛠 Админка › 🌍 Серверы › ✏️ <b>Редактирование</b>\n\n"
        "✏️ Введите новое имя сервера:",
        reply_markup=get_back_button("admin_servers")
    )
    await callback.answer()


@router.message(AdminStates.editing_server)
async def process_edit_server(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Пожалуйста, отправьте текстовое сообщение.")
        return
    if message.text.startswith("/") or message.text in REPLY_MENU_BUTTONS:
        await state.clear()
        await message.answer("⚠️ Операция прервана.", reply_markup=get_back_button("admin_servers"))
        return

    data = await state.get_data()
    server_id = data["server_id"]
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await message.answer("❌ Сервер не найден", show_alert=True)
            await state.clear()
            return
        new_name = message.text.strip()
        if len(new_name) > 255:
            await message.answer("⚠️ Слишком длинное имя (макс. 255 символов).")
            return
        await update_server(session, server, name=new_name)
        safe_new_name = html.escape(new_name)
        await message.answer(
            f"✅ Имя сервера изменено на: {safe_new_name}",
            reply_markup=get_back_button("admin_servers")
        )
        logger.info(f"Admin {message.from_user.id} updated server {server_id} name to {new_name}")

        flag = server.country_flag or "🌍"
        status = "🟢 Активен" if server.is_active else "🔴 Отключен"
        text = (
            f"🛠 Админка › 🌍 Серверы › {flag} <b>{safe_new_name}</b>\n\n"
            f"<b>ID:</b> {server.id}\n"
            f"<b>Статус:</b> {status}\n"
            f"<b>Протокол:</b> {server.protocol}\n"
            f"<b>API URL:</b> {server.api_url}\n"
            f"<b>Макс клиентов:</b> {server.max_clients}\n"
        )
        await message.answer(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
        await state.clear()
    finally:
        await session.close()
