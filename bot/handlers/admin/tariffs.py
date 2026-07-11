import logging
import math
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.keyboard import InlineKeyboardBuilder
from database.connection import get_session
from database.repositories.tariffs_repo import (
    get_all_tariffs, get_tariff_by_id, create_tariff, update_tariff, delete_tariff,
    get_tariff_count, get_tariffs_paginated
)
from bot.keyboards import get_admin_tariffs_keyboard, get_admin_tariff_card_keyboard, get_back_button
from bot.states import AdminStates
from config.settings import get_settings
from services.audit_service import AuditService

router = Router()
logger = logging.getLogger(__name__)

TARIFFS_PER_PAGE = 10


def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS


async def _build_tariffs_list_text_and_kb(tariffs, page: int, total_pages: int, total: int) -> tuple[str, InlineKeyboardBuilder]:
    """🔥 НОВОЕ: сборка текста и клавиатуры тарифов с пагинацией"""
    text = (
        f"🛠 Админка › 💰 <b>Тарифы</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n\n"
    )
    builder = InlineKeyboardBuilder()

    if not tariffs:
        text += "_Тарифов пока нет_\n"
    else:
        for tariff in tariffs:
            status = "🟢" if tariff.is_active else "🔴"
            btn_text = f"{status} {tariff.duration_days} дн. · {tariff.price_rub}₽ / {tariff.price_stars}⭐"
            builder.button(text=btn_text, callback_data=f"admin_tariff_card:{tariff.id}")

    nav_buttons = []
    if page > 1:
        nav_buttons.append(("⬅️", f"admin_tariffs_page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(("➡️", f"admin_tariffs_page:{page + 1}"))
    for btn_text, btn_data in nav_buttons:
        builder.button(text=btn_text, callback_data=btn_data)

    builder.button(text="➕ Добавить тариф", callback_data="admin_tariff_add")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return text, builder


@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    session = await get_session()
    try:
        total_tariffs = await get_tariff_count(session)
        total_pages = max(1, math.ceil(total_tariffs / TARIFFS_PER_PAGE))
        tariffs = await get_tariffs_paginated(session, page=1, per_page=TARIFFS_PER_PAGE)
        text, kb = await _build_tariffs_list_text_and_kb(tariffs, 1, total_pages, total_tariffs)
        try:
            await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariffs_page:"))
async def tariffs_pagination(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    page = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        total_tariffs = await get_tariff_count(session)
        total_pages = max(1, math.ceil(total_tariffs / TARIFFS_PER_PAGE))
        tariffs = await get_tariffs_paginated(session, page=page, per_page=TARIFFS_PER_PAGE)
        text, kb = await _build_tariffs_list_text_and_kb(tariffs, page, total_pages, total_tariffs)
        try:
            await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "admin_tariff_add")
async def start_add_tariff(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text(
        "🛠 Админка › 💰 Тарифы › ➕ <b>Новый тариф</b>\n\n"
        "⏱ Введите количество дней (число):",
        reply_markup=get_back_button("admin_tariffs")
    )
    await state.set_state(AdminStates.adding_tariff)
    await state.update_data(step="days")
    await callback.answer()


@router.message(AdminStates.adding_tariff)
async def process_add_tariff(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод.")
        return
    if message.text.startswith("/"):
        await state.clear()
        return

    data = await state.get_data()
    step = data.get("step")

    if step == "days":
        try:
            days = int(message.text.strip())
            if days < 1:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите число больше 0:")
            return
        await state.update_data(duration_days=days, step="price_rub")
        await message.answer(
            f"💵 Введите цену в рублях для {days} дней:",
            reply_markup=get_back_button("admin_tariffs")
        )
    elif step == "price_rub":
        try:
            price_rub = int(message.text.strip())
            if price_rub < 0:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите положительное число:")
            return
        await state.update_data(price_rub=price_rub, step="price_stars")
        await message.answer(
            "⭐ Введите цену в Stars:",
            reply_markup=get_back_button("admin_tariffs")
        )
    elif step == "price_stars":
        try:
            price_stars = int(message.text.strip())
            if price_stars <= 0:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите число больше 0 (Stars требует положительную сумму):")
            return

        all_data = await state.get_data()
        session = await get_session()
        try:
            tariff = await create_tariff(
                session, duration_days=all_data["duration_days"],
                price_rub=all_data["price_rub"], price_stars=price_stars
            )
            await AuditService.log_action(
                session, message.from_user.id, "ADD_TARIFF", "Tariff", tariff.id,
                f"{all_data['duration_days']} days, {all_data['price_rub']} RUB, {price_stars} Stars"
            )
            await message.answer(
                f"✅ Тариф добавлен!\n\n"
                f"⏱ <b>{all_data['duration_days']} дней</b>\n"
                f"💵 {all_data['price_rub']} ₽ / ⭐ {price_stars}",
                reply_markup=get_back_button("admin_tariffs"), parse_mode="HTML"
            )
            logger.info(f"Admin {message.from_user.id} added tariff {tariff.id}")
            await state.clear()
        finally:
            await session.close()


@router.callback_query(F.data.startswith("admin_tariff_card:"))
async def show_tariff_card(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        status = "🟢 Активен" if tariff.is_active else "🔴 Отключен"
        text = (
            f"🛠 Админка › 💰 Тарифы › <b>Тариф</b>\n\n"
            f"<b>ID:</b> {tariff.id}\n"
            f"<b>Дней:</b> {tariff.duration_days}\n"
            f"<b>Цена ₽:</b> {tariff.price_rub}\n"
            f"<b>Цена ⭐:</b> {tariff.price_stars}\n"
            f"<b>Статус:</b> {status}\n"
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_admin_tariff_card_keyboard(tariff.id, tariff.is_active),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_toggle:"))
async def toggle_tariff(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        new_status = not tariff.is_active
        await update_tariff(session, tariff, is_active=new_status)
        await AuditService.log_action(
            session, callback.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
            f"toggled to {'active' if new_status else 'inactive'}"
        )
        action = "включен" if new_status else "выключен"
        await callback.answer(f"✅ Тариф {action}", show_alert=True)
        logger.info(f"Admin {callback.from_user.id} toggled tariff {tariff_id} to {new_status}")

        tariff = await get_tariff_by_id(session, tariff_id)
        status = "🟢 Активен" if tariff.is_active else "🔴 Отключен"
        text = (
            f"🛠 Админка › 💰 Тарифы › <b>Тариф</b>\n\n"
            f"<b>ID:</b> {tariff.id}\n"
            f"<b>Дней:</b> {tariff.duration_days}\n"
            f"<b>Цена ₽:</b> {tariff.price_rub}\n"
            f"<b>Цена ⭐:</b> {tariff.price_stars}\n"
            f"<b>Статус:</b> {status}\n"
        )
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_admin_tariff_card_keyboard(tariff.id, tariff.is_active),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_delete:"))
async def delete_tariff_handler(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        await update_tariff(session, tariff, is_active=False)
        await AuditService.log_action(
            session, callback.from_user.id, "DELETE_TARIFF", "Tariff", tariff_id,
            f"{tariff.duration_days} days"
        )
        await callback.answer("✅ Тариф отключен", show_alert=True)
        logger.info(f"Admin {callback.from_user.id} disabled tariff {tariff_id}")

        # Возврат к списку с пагинацией
        total_tariffs = await get_tariff_count(session)
        total_pages = max(1, math.ceil(total_tariffs / TARIFFS_PER_PAGE))
        tariffs = await get_tariffs_paginated(session, page=1, per_page=TARIFFS_PER_PAGE)
        text, kb = await _build_tariffs_list_text_and_kb(tariffs, 1, total_pages, total_tariffs)
        try:
            await callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
        except TelegramBadRequest:
            pass
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_days:"))
async def start_edit_tariff_days(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_days)
    await callback.message.edit_text(
        "🛠 Админка › 💰 Тарифы › ⏱ <b>Изменить дни</b>\n\n"
        "⏱ Введите новое количество дней:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_days)
async def process_edit_tariff_days(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод. Отправьте число дней:")
        return
    if message.text.startswith("/"):
        await state.clear()
        await message.answer("⚠️ Операция прервана.")
        return
    try:
        days = int(message.text.strip())
        if days < 1:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число больше 0:")
        return
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await message.answer("❌ Тариф не найден", show_alert=True)
            await state.clear()
            return
        await update_tariff(session, tariff, duration_days=days)
        await AuditService.log_action(
            session, message.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
            f"days: {tariff.duration_days} -> {days}"
        )
        await message.answer(
            f"✅ Дни тарифа изменены на {days} дней",
            reply_markup=get_back_button("admin_tariffs")
        )
        logger.info(f"Admin {message.from_user.id} updated tariff {tariff_id} days to {days}")
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_rub:"))
async def start_edit_tariff_rub(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_rub)
    await callback.message.edit_text(
        "🛠 Админка › 💰 Тарифы › 💵 <b>Изменить цену ₽</b>\n\n"
        "💵 Введите новую цену в рублях:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_rub)
async def process_edit_tariff_rub(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод. Отправьте цену в рублях:")
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    try:
        price_rub = int(message.text.strip())
        if price_rub < 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите положительное число:")
        return
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await message.answer("❌ Тариф не найден", show_alert=True)
            await state.clear()
            return
        old_price = tariff.price_rub
        await update_tariff(session, tariff, price_rub=price_rub)
        await AuditService.log_action(
            session, message.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
            f"RUB: {old_price} -> {price_rub}"
        )
        await message.answer(
            f"✅ Цена в рублях изменена на {price_rub} ₽",
            reply_markup=get_back_button("admin_tariffs")
        )
        logger.info(f"Admin {message.from_user.id} updated tariff {tariff_id} price rub to {price_rub}")
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_stars:"))
async def start_edit_tariff_stars(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_stars)
    await callback.message.edit_text(
        "🛠 Админка › 💰 Тарифы › ⭐ <b>Изменить цену Stars</b>\n\n"
        "⭐ Введите новую цену в Stars:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_stars)
async def process_edit_tariff_stars(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await message.answer("⚠️ Ожидается текстовый ввод. Отправьте количество Stars:")
        return
    if message.text.startswith("/"):
        await state.clear()
        return
    try:
        price_stars = int(message.text.strip())
        if price_stars <= 0:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число больше 0 (Stars требует положительную сумму):")
        return
    data = await state.get_data()
    tariff_id = data["tariff_id"]
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await message.answer("❌ Тариф не найден", show_alert=True)
            await state.clear()
            return
        old_price = tariff.price_stars
        await update_tariff(session, tariff, price_stars=price_stars)
        await AuditService.log_action(
            session, message.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
            f"Stars: {old_price} -> {price_stars}"
        )
        await message.answer(
            f"✅ Цена в Stars изменена на {price_stars} ⭐",
            reply_markup=get_back_button("admin_tariffs")
        )
        logger.info(f"Admin {message.from_user.id} updated tariff {tariff_id} price stars to {price_stars}")
        await state.clear()
    finally:
        await session.close()