import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from config.settings import get_settings
from services.ban_service import BanService
from utils.admin import is_admin

from .common import (
    _get_user_with_profiles,
    _render_user_card,
)

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_ban:"))
async def admin_ban_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = int(callback.data.split(":")[1])

    settings = get_settings()

    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(
            texts.ERROR_ADMIN_BAN_FORBIDDEN,
            show_alert=True,
        )
        return

    text = (
        f"⚠️ <b>Подтверждение блокировки</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n"
        f"Пользователь будет заблокирован.\n"
        f"Все его устройства будут удалены без возможности "
        f"восстановления.\n"
        f"Ожидающие платежи будут отменены.\n"
        f"<i>После разблокировки устройства не восстанавливаются. "
        f"Пользователь сможет создать их заново, если подписка "
        f"активна.</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_ban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_ban_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_ban_apply:"))
async def admin_ban_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = int(callback.data.split(":")[1])

    settings = get_settings()

    if telegram_id in settings.ADMIN_IDS:
        await callback.answer(
            texts.ERROR_ADMIN_BAN_FORBIDDEN,
            show_alert=True,
        )
        return

    success, message = await BanService.toggle_ban(
        session,
        callback.from_user.id,
        telegram_id,
    )

    if not success:
        await callback.answer(
            f"❌ Ошибка: {message}",
            show_alert=True,
        )
        return

    await callback.answer(
        f"✅ Пользователь {message}",
        show_alert=True,
    )

    user = await _get_user_with_profiles(session, telegram_id)

    if user:
        await _render_user_card(callback, user, session)


@router.callback_query(F.data.startswith("admin_unban:"))
async def admin_unban_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = int(callback.data.split(":")[1])

    text = (
        f"⚠️ <b>Подтверждение разблокировки</b>\n"
        f"Пользователь: <code>{telegram_id}</code>\n"
        f"Пользователь будет разблокирован.\n"
        f"Устройства не будут восстановлены.\n"
        f"Пользователь сможет создать их заново, если подписка "
        f"активна.\n"
        f"<i>Это действие можно отменить повторной блокировкой.</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_admin_confirm_action_keyboard(
                confirm_callback=f"admin_unban_apply:{telegram_id}",
                cancel_callback=f"admin_user_card:{telegram_id}",
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"admin_unban_confirm edit_text failed: {e}")


@router.callback_query(F.data.startswith("admin_unban_apply:"))
async def admin_unban_apply(
    callback: CallbackQuery,
    session: AsyncSession,
):
    await callback.answer()

    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    telegram_id = int(callback.data.split(":")[1])

    success, message = await BanService.toggle_ban(
        session,
        callback.from_user.id,
        telegram_id,
    )

    if not success:
        await callback.answer(
            f"❌ Ошибка: {message}",
            show_alert=True,
        )
        return

    await callback.answer(
        f"✅ Пользователь {message}",
        show_alert=True,
    )

    user = await _get_user_with_profiles(session, telegram_id)

    if user:
        await _render_user_card(callback, user, session)