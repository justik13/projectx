# bot/handlers/admin/tariffs.py
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from database.connection import get_session
from database.repositories.tariffs_repo import (
    get_all_tariffs, get_tariff_by_id, create_tariff, 
    update_tariff, delete_tariff
)
from bot.keyboards import (
    get_admin_tariffs_keyboard, get_admin_tariff_card_keyboard, 
    get_back_button
)
from bot.states import AdminStates
from config.settings import get_settings

router = Router()
logger = logging.getLogger(__name__)


def is_admin(telegram_id: int) -> bool:
    settings = get_settings()
    return telegram_id in settings.ADMIN_IDS


@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(callback: CallbackQuery):
    """Показать список тарифов"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    session = await get_session()
    try:
        tariffs = await get_all_tariffs(session)
        
        text = "💰 Тарифы\n"
        text += "─────────────────────────────\n\n"
        
        if not tariffs:
            text += "_Тарифов пока нет_"
        else:
            for tariff in tariffs:
                status = "🟢" if tariff.is_active else "🔴"
                text += f"{status} <b>{tariff.duration_days} дней</b>\n"
                text += f"   {tariff.price_rub} ₽ / {tariff.price_stars} ⭐\n\n"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_admin_tariffs_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
    finally:
        await session.close()


@router.callback_query(F.data == "admin_tariff_add")
async def start_add_tariff(callback: CallbackQuery, state: FSMContext):
    """Начать добавление тарифа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "⏱ Введите количество дней (число):",
        reply_markup=get_back_button("admin_tariffs")
    )
    await state.set_state(AdminStates.adding_tariff)
    await state.update_data(step="days")
    await callback.answer()


@router.message(AdminStates.adding_tariff)
async def process_add_tariff(message: Message, state: FSMContext):
    """Обработать добавление тарифа (FSM)"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    data = await state.get_data()
    step = data.get("step")
    
    if step == "days":
        try:
            days = int(message.text.strip())
            if days < 1:
                raise ValueError("Дни не могут быть меньше 1")
        except ValueError:
            await message.answer("⚠️ Введите число больше 0. Попробуйте ещё раз:")
            return
        await state.update_data(duration_days=days, step="price_rub")
        await message.answer(
            f"💵 Введите цену в рублях для {days} дней:",
            reply_markup=get_back_button("admin_tariffs")
        )
    elif step == "price_rub":
        try:
            price_rub = int(message.text.strip())
            # ✅ Исправлено: Добавлена валидация на отрицательную стоимость в рублях
            if price_rub < 0:
                raise ValueError("Цена не может быть отрицательной")
        except ValueError:
            await message.answer("⚠️ Введите положительное число. Попробуйте ещё раз:")
            return
        await state.update_data(price_rub=price_rub, step="price_stars")
        await message.answer(
            f"⭐ Введите цену в Stars:",
            reply_markup=get_back_button("admin_tariffs")
        )
    elif step == "price_stars":
        try:
            price_stars = int(message.text.strip())
            # ✅ Исправлено: Защита P0. Telegram API требует, чтобы инвойс в Stars всегда был строго > 0
            if price_stars <= 0:
                raise ValueError("Цена в Stars должна быть строго больше нуля")
        except ValueError:
            await message.answer("⚠️ Введите число больше 0 (Telegram Stars требует положительную сумму). Попробуйте ещё раз:")
            return
        
        all_data = await state.get_data()
        session = await get_session()
        try:
            tariff = await create_tariff(
                session,
                duration_days=all_data["duration_days"],
                price_rub=all_data["price_rub"],
                price_stars=price_stars
            )
            
            await message.answer(
                f"✅ Тариф добавлен!\n\n"
                f"⏱ <b>{all_data['duration_days']} дней</b>\n"
                f"💵 {all_data['price_rub']} ₽ / ⭐ {price_stars}",
                reply_markup=get_back_button("admin_tariffs"),
                parse_mode="HTML"
            )
            
            logging.info(f"Admin {message.from_user.id} added tariff: {tariff.id}")
            await state.clear()
        finally:
            await session.close()


@router.callback_query(F.data.startswith("admin_tariff_card:"))
async def show_tariff_card(callback: CallbackQuery):
    """Показать карточку тарифа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        status = "🟢 Активен" if tariff.is_active else "🔴 Отключен"
        
        text = f"💰 Тариф\n"
        text += "─────────────────────────────\n\n"
        text += f"<b>ID:</b> {tariff.id}\n"
        text += f"<b>Дней:</b> {tariff.duration_days}\n"
        text += f"<b>Цена ₽:</b> {tariff.price_rub}\n"
        text += f"<b>Цена ⭐:</b> {tariff.price_stars}\n"
        text += f"<b>Статус:</b> {status}\n"
        
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
async def toggle_tariff(callback: CallbackQuery):
    """Включить/выключить тариф"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        new_status = not tariff.is_active
        await update_tariff(session, tariff, is_active=new_status)
        
        action = "включен" if new_status else "выключен"
        await callback.answer(f"✅ Тариф {action}", show_alert=True)
        
        logging.info(f"Admin {callback.from_user.id} toggled tariff {tariff_id} to {new_status}")
        
        # Обновляем карточку
        tariff = await get_tariff_by_id(session, tariff_id)
        status = "🟢 Активен" if tariff.is_active else "🔴 Отключен"
        
        text = f"💰 Тариф\n"
        text += "─────────────────────────────\n\n"
        text += f"<b>ID:</b> {tariff.id}\n"
        text += f"<b>Дней:</b> {tariff.duration_days}\n"
        text += f"<b>Цена ₽:</b> {tariff.price_rub}\n"
        text += f"<b>Цена ⭐:</b> {tariff.price_stars}\n"
        text += f"<b>Статус:</b> {status}\n"
        
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
async def delete_tariff_handler(callback: CallbackQuery):
    """Отключить тариф (soft delete)"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    session = await get_session()
    try:
        tariff = await get_tariff_by_id(session, tariff_id)
        if not tariff:
            await callback.answer("❌ Тариф не найден", show_alert=True)
            return
        
        # Отключаем тариф вместо удаления
        await update_tariff(session, tariff, is_active=False)
        
        await callback.answer("✅ Тариф отключен", show_alert=True)
        logging.info(f"Admin {callback.from_user.id} disabled tariff {tariff_id}")
        
        # Возвращаемся к списку
        tariffs = await get_all_tariffs(session)
        
        text = "💰 Тарифы\n"
        text += "─────────────────────────────\n\n"
        
        if not tariffs:
            text += "_Тарифов пока нет_"
        else:
            for t in tariffs:
                status = "🟢" if t.is_active else "🔴"
                text += f"{status} <b>{t.duration_days} дней</b>\n"
                text += f"   {t.price_rub} ₽ / {t.price_stars} ⭐\n\n"
        
        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_admin_tariffs_keyboard(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_days:"))
async def start_edit_tariff_days(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование дней тарифа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_days)
    
    await callback.message.edit_text(
        "⏱ Введите новое количество дней:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_days)
async def process_edit_tariff_days(message: Message, state: FSMContext):
    """Обработать редактирование дней тарифа"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        days = int(message.text.strip())
        if days < 1:
            raise ValueError("Дни не могут быть меньше 1")
    except ValueError:
        await message.answer("⚠️ Введите число больше 0. Попробуйте ещё раз:")
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
        
        await message.answer(
            f"✅ Дни тарифа изменены на {days} дней",
            reply_markup=get_back_button("admin_tariffs")
        )
        
        logging.info(f"Admin {message.from_user.id} updated tariff {tariff_id} days to {days}")
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_rub:"))
async def start_edit_tariff_rub(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование цены в рублях тарифа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_rub)
    
    await callback.message.edit_text(
        "💵 Введите новую цену в рублях:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_rub)
async def process_edit_tariff_rub(message: Message, state: FSMContext):
    """Обработать редактирование цены в рублях тарифа"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        price_rub = int(message.text.strip())
        if price_rub < 0:
            raise ValueError("Цена не может быть отрицательной")
    except ValueError:
        await message.answer("⚠️ Введите положительное число. Попробуйте ещё раз:")
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
        
        await update_tariff(session, tariff, price_rub=price_rub)
        
        await message.answer(
            f"✅ Цена в рублях тарифа изменена на {price_rub} ₽",
            reply_markup=get_back_button("admin_tariffs")
        )
        
        logging.info(f"Admin {message.from_user.id} updated tariff {tariff_id} price rub to {price_rub}")
        await state.clear()
    finally:
        await session.close()


@router.callback_query(F.data.startswith("admin_tariff_edit_stars:"))
async def start_edit_tariff_stars(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование цены в stars тарифа"""
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔️ Нет доступа", show_alert=True)
        return
    
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(AdminStates.editing_tariff_stars)
    
    await callback.message.edit_text(
        "⭐ Введите новую цену в Stars:",
        reply_markup=get_back_button("admin_tariffs")
    )
    await callback.answer()


@router.message(AdminStates.editing_tariff_stars)
async def process_edit_tariff_stars(message: Message, state: FSMContext):
    """Обработать редактирование цены в stars тарифа"""
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    
    try:
        price_stars = int(message.text.strip())
        # ✅ Исправлено: Защита P0. Не даем выставить Stars <= 0
        if price_stars <= 0:
            raise ValueError("Цена в Stars должна быть строго больше нуля")
    except ValueError:
        await message.answer("⚠️ Введите число больше 0 (Telegram Stars требует положительную сумму):")
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
        
        await update_tariff(session, tariff, price_stars=price_stars)
        
        await message.answer(
            f"✅ Цена в Stars тарифа изменена на {price_stars} ⭐",
            reply_markup=get_back_button("admin_tariffs")
        )
        
        logging.info(f"Admin {message.from_user.id} updated tariff {tariff_id} price stars to {price_stars}")
        await state.clear()
    finally:
        await session.close()