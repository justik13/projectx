import asyncio
import logging

from aiogram import Router, F
from aiogram.exceptions import (
    TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.states import AdminStates
from bot import texts
from database.connection import session_scope
from database.repositories.users_repo import (
    get_active_users, get_all_users, mark_user_bot_blocked,
)
from services.audit_service import AuditService
from utils.admin import is_admin

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await callback.message.edit_text(
        texts.BROADCAST_PROMPT,
        reply_markup=get_back_button("admin_menu"),
    )
    await state.set_state(AdminStates.entering_broadcast_message)
    await callback.answer()


@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await message.answer(texts.ERROR_TEXT_OR_MEDIA)
        return

    media_id = None
    content_type = message.content_type

    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id

    preview = texts.BROADCAST_PREVIEW.format(
        content_type=content_type, text=broadcast_text,
    )

    try:
        if media_id and content_type == "photo":
            await message.answer_photo(
                media_id, caption=preview,
                reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        elif media_id and content_type == "document":
            await message.answer_document(
                media_id, caption=preview,
                reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        else:
            await message.answer(
                preview,
                reply_markup=get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
        await state.update_data(
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
        )
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await message.answer(texts.ERROR_VALIDATION.format(error=e))


async def _send_broadcast_to_users(
    callback: CallbackQuery,
    user_ids: list[int],
    broadcast_text: str,
    media_id: str | None,
    content_type: str,
    label: str,
):
    total_count = len(user_ids)
    success_count = 0
    fail_count = 0

    await callback.message.edit_text(
        f"⏳ Начинаю рассылку для {total_count} пользователей...",
    )

    for uid in user_ids:
        try:
            await _dispatch_message(
                callback.bot, uid, broadcast_text, media_id, content_type,
            )
            success_count += 1
            await asyncio.sleep(0.04)

        except TelegramRetryAfter as e:
            fail_count += 1
            logger.warning(f"Flood wait for user {uid}: sleeping {e.retry_after + 1}s")
            await asyncio.sleep(e.retry_after + 1)
            try:
                await _dispatch_message(
                    callback.bot, uid, broadcast_text, media_id, content_type,
                )
                success_count += 1
                fail_count -= 1
            except Exception:
                pass

        except TelegramForbiddenError:
            fail_count += 1
            logger.info(f"User {uid} blocked the bot")
            try:
                async with session_scope() as session:
                    await mark_user_bot_blocked(session, uid)
            except Exception as e:
                logger.error(f"Failed to mark user {uid} as bot_blocked: {e}")

        except Exception as e:
            fail_count += 1
            logger.warning(f"Failed to send broadcast to {uid}: {e}")

    await callback.message.edit_text(
        texts.BROADCAST_RESULT.format(
            success_count=success_count,
            fail_count=fail_count,
            label=label,
            total_count=total_count,
        ),
        reply_markup=get_back_button("admin_menu"),
    )

    logger.info(
        f"Admin {callback.from_user.id} broadcast to {label}: "
        f"success={success_count}, fail={fail_count}",
    )

    try:
        async with session_scope() as session:
            await AuditService.log_action(
                session,
                callback.from_user.id,
                "BROADCAST",
                details=f"to {label}: {success_count} success, {fail_count} fail",
            )
    except Exception as e:
        logger.error(f"Failed to log broadcast action: {e}")


async def _dispatch_message(bot, uid, text, media_id, content_type):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, parse_mode="HTML")
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, parse_mode="HTML")
    else:
        await bot.send_message(uid, text, parse_mode="HTML")


@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(texts.ERROR_TEXT_EMPTY, show_alert=True)
        await state.clear()
        return

    media_id = data.get("media_id")
    content_type = data.get("content_type")

    users = await get_all_users(session)
    user_ids = [u.telegram_id for u in users if not u.is_bot_blocked]

    await _send_broadcast_to_users(
        callback, user_ids, broadcast_text, media_id, content_type, "Всего",
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(texts.ERROR_TEXT_EMPTY, show_alert=True)
        await state.clear()
        return

    media_id = data.get("media_id")
    content_type = data.get("content_type")

    users = await get_active_users(session)
    user_ids = [u.telegram_id for u in users]

    await _send_broadcast_to_users(
        callback, user_ids, broadcast_text, media_id, content_type, "Активных",
    )
    await state.clear()
    await callback.answer()