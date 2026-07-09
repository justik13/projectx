# bot/handlers/admin/servers.py
import html
import logging
import asyncio

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database.connection import get_session
from database.repositories.servers_repo import (
    get_all_servers, get_server_by_id, create_server, 
    update_server, delete_server
)
from database.repositories.profiles_repo import update_profile
from bot.keyboards import (
    get_admin_servers_keyboard, get_admin_server_card_keyboard, 
    get_back_button
)
from bot.states import AdminStates
from config.settings import get_settings

router = Router()


def is_admin(telegram_id: int) -> bool:
    settings = get_settings()
    return telegram_id in settings.ADMIN_IDS


@router.callback_query(F.data == "admin_servers")
async def show_servers_list(callback: CallbackQuery):
    """Показать список серверов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    session = await get_session()
    try:
        servers = await get_all_servers(session)
        
        text = "🌍 Серверы\n"
        text += "─────────────────────────────\n\n"
        
        if not servers:
            text += "_Серверов пока нет_\n\nНажмите [➕ Добавить сервер]"
        else:
            for server in servers:
                flag = server.country_flag or "🌍"
                status = "🟢" if server.is_active else "🔴"
                safe_name = html.escape(server.name)
                text += f"{status} {flag} <b>{safe_name}</b>\n"
                text += f"   {server.protocol} · {server.api_url}\n\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_servers_keyboard(),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "admin_server_add")
async def start_add_server(callback: CallbackQuery, state: FSMContext):
    """Начать добавление сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "✏️ Введите имя сервера (например: Нидерланды):",
        reply_markup=get_back_button("admin_servers")
    )
    await state.set_state(AdminStates.adding_server)
    await state.update_data(step="name")
    await callback.answer()


@router.message(AdminStates.adding_server)
async def process_add_server(message: Message, state: FSMContext):
    """Обработать добавление сервера (FSM)"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    step = data.get("step")
    
    if step == "name":
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
        await state.update_data(api_url=message.text.strip(), step="api_key")
        await message.answer(
            "🔑 Введите API ключ сервера:",
            reply_markup=get_back_button("admin_servers")
        )
    elif step == "api_key":
        await state.update_data(api_key=message.text.strip(), step="max_clients")
        await message.answer(
            "👥 Введите максимальное количество клиентов (число):",
            reply_markup=get_back_button("admin_servers")
        )
    elif step == "max_clients":
        try:
            max_clients = int(message.text.strip())
        except ValueError:
            await message.answer("⚠️ Введите число. Попробуйте ещё раз:")
            return
        
        all_data = await state.get_data()
        session = await get_session()
        try:
            server = await create_server(
                session,
                name=all_data["name"],
                country_flag=all_data["country_flag"],
                api_url=all_data["api_url"],
                api_key=all_data["api_key"],
                protocol="amneziawg2",
                max_clients=max_clients
            )
            
            safe_name = html.escape(all_data["name"])
            await message.answer(
                f"✅ Сервер добавлен!\n\n"
                f"{all_data['country_flag']} <b>{safe_name}</b>\n"
                f"Протокол: amneziawg2\n"
                f"Макс клиентов: {max_clients}",
                reply_markup=get_back_button("admin_servers"),
                parse_mode="HTML"
            )
            
            logging.info(f"Admin {message.from_user.id} added server: {server.id}")
            await state.clear()
        finally:
            await session.close()


@router.callback_query(F.data.startswith("admin_server_card:"))
async def show_server_card(callback: CallbackQuery):
    """Показать карточку сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
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
        
        text = f"{flag} Сервер: {safe_name}\n"
        text += "─────────────────────────────\n\n"
        text += f"<b>ID:</b> {server.id}\n"
        text += f"<b>Статус:</b> {status}\n"
        text += f"<b>Протокол:</b> {server.protocol}\n"
        text += f"<b>API URL:</b> {server.api_url}\n"
        text += f"<b>Макс клиентов:</b> {server.max_clients}\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_toggle:"))
async def toggle_server(callback: CallbackQuery):
    """Включить/выключить сервер"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.answer("⏳ Выполняется...")
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        
        new_status = not server.is_active
        await update_server(session, server, is_active=new_status)
        
        action = "включен" if new_status else "выключен"
        await callback.answer(f"✅ Сервер {action}", show_alert=True)
        
        logging.info(f"Admin {callback.from_user.id} toggled server {server_id} to {new_status}")
        
        # Обновляем карточку
        server = await get_server_by_id(session, server_id)
        flag = server.country_flag or "🌍"
        status = "🟢 Активен" if server.is_active else "🔴 Отключен"
        safe_name = html.escape(server.name)
        
        text = f"{flag} Сервер: {safe_name}\n"
        text += "─────────────────────────────\n\n"
        text += f"<b>ID:</b> {server.id}\n"
        text += f"<b>Статус:</b> {status}\n"
        text += f"<b>Протокол:</b> {server.protocol}\n"
        text += f"<b>API URL:</b> {server.api_url}\n"
        text += f"<b>Макс клиентов:</b> {server.max_clients}\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_delete:"))
async def delete_server_handler(callback: CallbackQuery):
    """Отключить сервер (soft delete) с параллельной деактивацией пиров"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.answer("⏳ Выполняется параллельное отключение пиров...")
    server_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        server = await get_server_by_id(session, server_id)
        if not server:
            await callback.answer("❌ Сервер не найден", show_alert=True)
            return
        
        # Получаем все профили, привязанные к этому серверу
        from sqlalchemy import select
        from database.models import VPNProfile
        result = await session.execute(
            select(VPNProfile).where(VPNProfile.server_id == server.id)
        )
        profiles = result.scalars().all()
        
        # Оптимизация Highload: собираем конкурентные сетевые задачи
        client = AmneziaClient(server.api_url, server.api_key)
        tasks = []
        profiles_to_update = []
        
        for profile in profiles:
            tasks.append(client.update_client(client_id=profile.peer_id, status="disabled"))
            profiles_to_update.append(profile)
        
        if tasks:
            # Отключаем всех клиентов на удаляемом VPS одновременно
            await asyncio.gather(*tasks, return_exceptions=True)
            for p in profiles_to_update:
                p.is_active = False
            # Фиксируем изменения в локальной БД ОДНИМ коммитом вместо N коммитов в цикле
            await session.commit()
        
        # Отключаем сам сервер
        await update_server(session, server, is_active=False)
        
        await callback.answer("✅ Сервер и связанные устройства успешно отключены", show_alert=True)
        logging.info(f"Admin {callback.from_user.id} disabled server {server_id} with {len(profiles)} profiles")
        
        # Возвращаемся к списку серверов
        servers = await get_all_servers(session)
        
        text = "🌍 Серверы\n"
        text += "─────────────────────────────\n\n"
        
        if not servers:
            text += "_Серверов пока нет_"
        else:
            for s in servers:
                flag = s.country_flag or "🌍"
                status = "🟢" if s.is_active else "🔴"
                safe_s_name = html.escape(s.name)
                text += f"{status} {flag} <b>{safe_s_name}</b>\n"
                text += f"   {s.protocol} · {s.api_url}\n\n"
        
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_servers_keyboard(),
            parse_mode="HTML"
        )
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_server_edit:"))
async def start_edit_server(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование сервера"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    server_id = int(callback.data.split(":")[1])
    await state.update_data(server_id=server_id)
    await state.set_state(AdminStates.editing_server)
    
    await callback.message.edit_text(
        "✏️ Введите новое имя сервера:",
        reply_markup=get_back_button("admin_servers")
    )
    await callback.answer()


@router.message(AdminStates.editing_server)
async def process_edit_server(message: Message, state: FSMContext):
    """Обработать редактирование сервера"""
    if not is_admin(message.from_user.id):
        await state.clear()
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
        await update_server(session, server, name=new_name)
        
        safe_new_name = html.escape(new_name)
        await message.answer(
            f"✅ Имя сервера изменено на: {safe_new_name}",
            reply_markup=get_back_button("admin_servers")
        )
        
        logging.info(f"Admin {message.from_user.id} updated server {server_id} name to {new_name}")
        
        # Возвращаемся к карточке сервера
        flag = server.country_flag or "🌍"
        status = "🟢 Активен" if server.is_active else "🔴 Отключен"
        
        text = f"{flag} Сервер: {safe_new_name}\n"
        text += "─────────────────────────────\n\n"
        text += f"<b>ID:</b> {server.id}\n"
        text += f"<b>Статус:</b> {status}\n"
        text += f"<b>Протокол:</b> {server.protocol}\n"
        text += f"<b>API URL:</b> {server.api_url}\n"
        text += f"<b>Макс клиентов:</b> {server.max_clients}\n"
        
        await message.answer(
            text,
            reply_markup=get_admin_server_card_keyboard(server.id, server.is_active),
            parse_mode="HTML"
        )
        await state.clear()
    finally:
        await session.close()