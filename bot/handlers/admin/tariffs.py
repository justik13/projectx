import logging
import math
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from bot.keyboards import get_admin_tariff_card_keyboard, get_back_button
from bot.states import AdminStates
from bot import texts
from database.repositories.tariffs_repo import (
    get_tariff_by_id, get_tariff_count, get_tariffs_paginated,
    update_tariff, delete_tariff,
)
from database.repositories.users_repo import count_users_with_tariff
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.telegram import render_hub

router = Router()
logger = logging.getLogger(__name__)

TARIFFS_PER_PAGE = 10


async def _build_tariffs_list_text_and_kb(
    tariffs, page: int, total_pages: int, total: int,
) -> tuple[str, InlineKeyboardBuilder]:
    rendered = (
        f"🛠 Админка › 💰 <b>Тарифы</b>\n"
        f"(стр. {page}/{total_pages}) · Всего: {total}\n"
    )
    builder = InlineKeyboardBuilder()
    if not tariffs:
        rendered += "<i>Тарифов пока нет</i>\n"
    else:
        for tariff in tariffs:
            status = "🟢" if tariff.is_active else "🔴"
            device_limit = getattr(tariff, 'device_limit', 2)
            builder.button(
                text=(
                    f"{status} {tariff.duration_days} дн. · "
                    f"{device_limit} устр. · "
                    f"{tariff.price_rub}₽ / {tariff.price_stars}⭐"
                ),
                callback_data=f"admin_tariff_card:{tariff.id}",
            )

    if page > 1:
        builder.button(text="⬅️", callback_data=f"admin_tariffs_page:{page - 1}")
    if page < total_pages:
        builder.button(text="➡️", callback_data=f"admin_tariffs_page:{page + 1}")
    builder.button(text="← В админку", callback_data="admin_menu")
    builder.adjust(1)
    return rendered, builder


async def _show_tariffs_list(callback: CallbackQuery, session: AsyncSession, page: int = 1):
    total_tariffs = await get_tariff_count(session)
    total_pages = max(1, math.ceil(total_tariffs / TARIFFS_PER_PAGE))
    tariffs = await get_tariffs_paginated(session, page=page, per_page=TARIFFS_PER_PAGE)
    rendered, kb = await _build_tariffs_list_text_and_kb(tariffs, page, total_pages, total_tariffs)
    try:
        await callback.message.edit_text(rendered, reply_markup=kb.as_markup(), parse_mode="HTML")
    except TelegramBadRequest as e:
        # 🔥 MUST FIX #6: Логируем вместо pass
        logger.debug(f"_show_tariffs_list edit_text failed: {e}")


@router.callback_query(F.data == "admin_tariffs")
async def show_tariffs_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    await _show_tariffs_list(callback, session, page=1)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tariffs_page:"))
async def tariffs_pagination(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    page = int(callback.data.split(":")[1])
    await _show_tariffs_list(callback, session, page=page)
    await callback.answer()


async def _show_tariff_card(callback: CallbackQuery, tariff):
    status = "🟢 Активен" if tariff.is_active else "🔴 Отключен"
    device_limit = getattr(tariff, 'device_limit', 2)
    rendered = texts.ADMIN_TARIFF_CARD.format(
        id=tariff.id, duration_days=tariff.duration_days,
        device_limit=device_limit, price_rub=tariff.price_rub,
        price_stars=tariff.price_stars, status=status,
    )
    try:
        await callback.message.edit_text(
            rendered,
            reply_markup=get_admin_tariff_card_keyboard(tariff.id, tariff.is_active),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        # 🔥 MUST FIX #6: Логируем вместо pass
        logger.debug(f"_show_tariff_card edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_tariff_card:"))
async def show_tariff_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(texts.ERROR_TARIFF_NOT_FOUND, show_alert=True)
        return
    await _show_tariff_card(callback, tariff)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_tariff_toggle:"))
async def toggle_tariff(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(texts.ERROR_TARIFF_NOT_FOUND, show_alert=True)
        return
    new_status = not tariff.is_active
    await update_tariff(session, tariff, is_active=new_status)
    await AuditService.log_action(
        session, callback.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
        f"toggled to {'active' if new_status else 'inactive'}",
    )
    await callback.answer(f"✅ Тариф {'включен' if new_status else 'выключен'}", show_alert=True)
    refreshed = await get_tariff_by_id(session, tariff_id)
    await _show_tariff_card(callback, refreshed)


@router.callback_query(F.data.startswith("admin_tariff_delete:"))
async def delete_tariff_handler(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await callback.answer(texts.ERROR_TARIFF_NOT_FOUND, show_alert=True)
        return
    user_count = await count_users_with_tariff(session, tariff_id)
    if user_count > 0:
        try:
            await callback.message.edit_text(
                texts.ERROR_TARIFF_IN_USE.format(user_count=user_count),
                reply_markup=get_back_button(f"admin_tariff_card:{tariff_id}"),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            # 🔥 MUST FIX #6: Логируем вместо pass
            logger.debug(f"delete_tariff_handler edit_text failed: {e}")
        await callback.answer()
        return

    device_limit = getattr(tariff, 'device_limit', 2)
    await delete_tariff(session, tariff)
    await AuditService.log_action(
        session, callback.from_user.id, "DELETE_TARIFF", "Tariff", tariff_id,
        f"{tariff.duration_days}d/{device_limit}dev/{tariff.price_rub}rub",
    )
    await callback.answer(f"✅ Тариф ({tariff.duration_days} дн., {device_limit} устр.) удалён", show_alert=True)
    await _show_tariffs_list(callback, session, page=1)


async def _start_edit_tariff(
    callback: CallbackQuery, state: FSMContext, field_state, prompt_text: str,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await state.clear()
    tariff_id = int(callback.data.split(":")[1])
    await state.update_data(tariff_id=tariff_id)
    await state.set_state(field_state)
    try:
        await callback.message.edit_text(
            prompt_text, reply_markup=get_back_button("admin_tariffs"),
        )
    except TelegramBadRequest as e:
        # 🔥 MUST FIX #6: Логируем вместо pass
        logger.debug(f"_start_edit_tariff edit_text failed: {e}")
    await callback.answer()


async def _apply_tariff_int_edit(
    message: Message, state: FSMContext, session: AsyncSession,
    *, field_name: str, validator, validator_error: str,
    success_message, audit_detail_fn,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    if not message.text:
        await render_hub(message.bot, message.chat.id, validator_error, get_back_button("admin_tariffs"))
        return
    if message.text.startswith("/"):
        await state.clear()
        await render_hub(message.bot, message.chat.id, texts.ERROR_OPERATION_CANCELLED, get_back_button("admin_tariffs"))
        return
    try:
        new_value = int(message.text.strip())
        if not validator(new_value):
            raise ValueError
    except ValueError:
        await render_hub(message.bot, message.chat.id, validator_error, get_back_button("admin_tariffs"))
        return

    data = await state.get_data()
    tariff_id = data["tariff_id"]
    tariff = await get_tariff_by_id(session, tariff_id)
    if not tariff:
        await render_hub(message.bot, message.chat.id, texts.ERROR_TARIFF_NOT_FOUND, get_back_button("admin_tariffs"))
        await state.clear()
        return

    old_value = getattr(tariff, field_name)
    await update_tariff(session, tariff, **{field_name: new_value})
    await AuditService.log_action(
        session, message.from_user.id, "EDIT_TARIFF", "Tariff", tariff_id,
        audit_detail_fn(old_value, new_value),
    )
    await render_hub(message.bot, message.chat.id, success_message(new_value), get_back_button("admin_tariffs"))
    logger.info(f"Admin {message.from_user.id} updated tariff {tariff_id} {field_name}: {old_value} -> {new_value}")
    await state.clear()


@router.callback_query(F.data.startswith("admin_tariff_edit_days:"))
async def start_edit_tariff_days(callback: CallbackQuery, state: FSMContext):
    await _start_edit_tariff(
        callback, state, AdminStates.editing_tariff_days, texts.ADMIN_TARIFF_EDIT_DAYS_PROMPT,
    )


@router.message(AdminStates.editing_tariff_days)
async def process_edit_tariff_days(message: Message, state: FSMContext, session: AsyncSession):
    await _apply_tariff_int_edit(
        message, state, session, field_name="duration_days",
        validator=lambda x: x >= 1, validator_error=texts.ERROR_NUMBER_GT_ZERO,
        success_message=lambda v: f"✅ Дни тарифа изменены на {v} дней",
        audit_detail_fn=lambda old, new: f"days: {old} -> {new}",
    )


@router.callback_query(F.data.startswith("admin_tariff_edit_devices:"))
async def start_edit_tariff_devices(callback: CallbackQuery, state: FSMContext):
    await _start_edit_tariff(
        callback, state, AdminStates.editing_tariff_device_limit,
        texts.ADMIN_TARIFF_EDIT_DEVICES_PROMPT,
    )


@router.message(AdminStates.editing_tariff_device_limit)
async def process_edit_tariff_devices(message: Message, state: FSMContext, session: AsyncSession):
    await _apply_tariff_int_edit(
        message, state, session, field_name="device_limit",
        validator=lambda x: x >= 1, validator_error=texts.ERROR_NUMBER_GT_ZERO,
        success_message=lambda v: f"✅ Лимит устройств изменён на {v}",
        audit_detail_fn=lambda old, new: f"device_limit: {old} -> {new}",
    )


@router.callback_query(F.data.startswith("admin_tariff_edit_rub:"))
async def start_edit_tariff_rub(callback: CallbackQuery, state: FSMContext):
    await _start_edit_tariff(
        callback, state, AdminStates.editing_tariff_rub, texts.ADMIN_TARIFF_EDIT_RUB_PROMPT,
    )


@router.message(AdminStates.editing_tariff_rub)
async def process_edit_tariff_rub(message: Message, state: FSMContext, session: AsyncSession):
    await _apply_tariff_int_edit(
        message, state, session, field_name="price_rub",
        validator=lambda x: x >= 0, validator_error=texts.ERROR_POSITIVE_NUMBER,
        success_message=lambda v: f"✅ Цена в рублях изменена на {v} ₽",
        audit_detail_fn=lambda old, new: f"RUB: {old} -> {new}",
    )


@router.callback_query(F.data.startswith("admin_tariff_edit_stars:"))
async def start_edit_tariff_stars(callback: CallbackQuery, state: FSMContext):
    await _start_edit_tariff(
        callback, state, AdminStates.editing_tariff_stars, texts.ADMIN_TARIFF_EDIT_STARS_PROMPT,
    )


@router.message(AdminStates.editing_tariff_stars)
async def process_edit_tariff_stars(message: Message, state: FSMContext, session: AsyncSession):
    await _apply_tariff_int_edit(
        message, state, session, field_name="price_stars",
        validator=lambda x: x > 0, validator_error=texts.ERROR_STARS_POSITIVE,
        success_message=lambda v: f"✅ Цена в Stars изменена на {v} ⭐",
        audit_detail_fn=lambda old, new: f"Stars: {old} -> {new}",
    )