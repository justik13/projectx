import asyncio
import logging
from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_broadcast_confirm_keyboard, get_back_button
from bot.keyboards.admin.broadcast import (
    get_broadcast_result_keyboard, get_broadcast_close_keyboard
)
from bot.states import AdminStates
from bot import texts
from bot.constants import BROADCAST_DELAY
from database.connection import session_scope
from database.models import BroadcastProgress, User
from database.repositories.users_repo import mark_user_bot_blocked
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc
from utils.telegram import render_hub, send_hub_photo

router = Router()
logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО #10: cleanup добавлен в finally
_broadcast_stop_events: dict[int, asyncio.Event] = {}
_broadcast_in_progress: set[int] = set()

# ═══════════════════════════════════════════════════════════
# 🔥 ИСПРАВЛЕНО P0-1: Рекурсия заменена на while True
# ═══════════════════════════════════════════════════════════
class BroadcastRateLimiter:
    def __init__(self, rate: float = 20.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = asyncio.get_event_loop().time()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = asyncio.get_event_loop().time()
                elapsed = now - self.last_refill
                self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
            # Sleep ВНЕ lock — другие acquire() могут проходить
            await asyncio.sleep(1.0 / self.rate)

_broadcast_limiter = BroadcastRateLimiter(rate=20.0)

def _get_stop_event(admin_id: int) -> asyncio.Event:
    if admin_id not in _broadcast_stop_events:
        _broadcast_stop_events[admin_id] = asyncio.Event()
    return _broadcast_stop_events[admin_id]

def _cleanup_stop_event(admin_id: int) -> None:
    """
    🔥 ИСПРАВЛЕНО #10: Удаляет stop_event из словаря после завершения рассылки.
    """
    _broadcast_stop_events.pop(admin_id, None)

@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return

    await state.clear()
    try:
        await callback.message.edit_text(
            texts.BROADCAST_PROMPT, reply_markup=get_back_button("admin_menu"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_broadcast edit_text failed: {e}")
        
    await state.set_state(AdminStates.entering_broadcast_message)

@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_TEXT_OR_MEDIA, get_back_button("admin_menu")
        )
        return

    media_id = None
    content_type = message.content_type
    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id

    preview = texts.BROADCAST_PREVIEW.format(content_type=content_type, text=broadcast_text)
    try:
        if media_id and content_type == "photo":
            await send_hub_photo(
                message.bot, message.chat.id, message.photo[-1],
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        elif media_id and content_type == "document":
            from utils.telegram import send_hub_document
            await send_hub_document(
                message.bot, message.chat.id, message.document,
                caption=preview, reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        else:
            await render_hub(
                message.bot, message.chat.id, preview,
                get_broadcast_confirm_keyboard(), parse_mode="HTML",
            )
            
        await state.update_data(broadcast_text=broadcast_text, media_id=media_id, content_type=content_type)
        await state.set_state(AdminStates.confirming_broadcast)
    except Exception as e:
        await render_hub(
            message.bot, message.chat.id,
            texts.ERROR_VALIDATION.format(error=e),
            get_back_button("admin_menu")
        )

async def _send_with_html(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

async def _send_plain(bot, uid, text, media_id, content_type, kb):
    if content_type == "photo" and media_id:
        await bot.send_photo(uid, media_id, caption=text, reply_markup=kb)
    elif content_type == "document" and media_id:
        await bot.send_document(uid, media_id, caption=text, reply_markup=kb)
    else:
        await bot.send_message(uid, text, reply_markup=kb)

async def _dispatch_message(bot, uid, text, media_id, content_type):
    kb = get_broadcast_close_keyboard()
    try:
        await _send_with_html(bot, uid, text, media_id, content_type, kb)
    except TelegramBadRequest as e:
        if "can't parse entities" in str(e).lower() or "parse" in str(e).lower():
            logger.warning(f"HTML parse failed for user {uid}, falling back to plain text")
            await _send_plain(bot, uid, text, media_id, content_type, kb)
        else:
            raise

async def _get_next_batch(session: AsyncSession, audience: str, last_id: int, limit: int = 50):
    """🔥 ИСПРАВЛЕНО: Получение пачки юзеров курсором из БД вместо JSON-массива"""
    stmt = select(User.telegram_id).where(
        User.telegram_id > last_id,
        User.is_deleted == False,
        User.is_bot_blocked == False
    )
    if audience == "active":
        current_time = now_utc()
        stmt = stmt.where(User.subscription_end > current_time, User.is_banned == False)
        
    stmt = stmt.order_by(User.telegram_id).limit(limit)
    result = await session.execute(stmt)
    return [row[0] for row in result.all()]

async def _send_broadcast_to_users_with_resume(bot, progress_id: int):
    """
    🔥 ИСПРАВЛЕНО: Возобновляемая рассылка с прогрессом в БД.
    """
    stop_event = None
    admin_id = None
    broadcast_text = None
    media_id = None
    content_type = None
    target_audience = None
    last_id = None
    total_count = None

    async with session_scope() as session:
        progress = await session.get(BroadcastProgress, progress_id)
        if not progress or progress.status != "in_progress":
            return
            
        admin_id = progress.admin_id
        stop_event = _get_stop_event(admin_id)
        stop_event.clear()
        broadcast_text = progress.broadcast_text
        media_id = progress.media_id
        content_type = progress.content_type
        target_audience = progress.target_audience
        last_id = progress.last_processed_id
        total_count = progress.total_count

    logger.info(f"Broadcast resume: admin={admin_id}, progress_id={progress_id}, starting from id {last_id}")
    
    blocked_user_ids = []
    local_success = 0
    local_fail = 0
    
    try:
        while True:
            if stop_event and stop_event.is_set():
                break
                
            async with session_scope() as session:
                batch = await _get_next_batch(session, target_audience, last_id)
                if not batch:
                    break
                    
                for uid in batch:
                    if stop_event and stop_event.is_set():
                        break
                    try:
                        await _broadcast_limiter.acquire()
                        await _dispatch_message(bot, uid, broadcast_text, media_id, content_type)
                        local_success += 1
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        try:
                            await _broadcast_limiter.acquire()
                            await _dispatch_message(bot, uid, broadcast_text, media_id, content_type)
                            local_success += 1
                        except Exception:
                            local_fail += 1
                    except TelegramForbiddenError:
                        blocked_user_ids.append(uid)
                        local_fail += 1
                    except Exception as e:
                        logger.error(f"Broadcast error for user {uid}: {e}")
                        local_fail += 1
                        
                    last_id = uid

            async with session_scope() as session:
                progress = await session.get(BroadcastProgress, progress_id)
                if progress:
                    progress.last_processed_id = last_id
                    progress.success_count += local_success
                    progress.fail_count += local_fail
                    await session.commit()
                    
            local_success = 0
            local_fail = 0
            
    finally:
        # 🔥 ИСПРАВЛЕНО #10: Всегда очищаем stop_event
        if stop_event:
            stop_event.clear()
            
        final_progress = None
        async with session_scope() as session:
            progress = await session.get(BroadcastProgress, progress_id)
            if progress:
                progress.status = "completed" if not (stop_event and stop_event.is_set()) else "stopped"
                await session.commit()
                final_progress = progress

        if blocked_user_ids:
            try:
                async with session_scope() as session:
                    for uid in blocked_user_ids:
                        await mark_user_bot_blocked(session, uid)
            except Exception as e:
                logger.error(f"Failed to batch mark users as bot_blocked: {e}")

        if admin_id:
            _broadcast_in_progress.discard(admin_id)
            # 🔥 ИСПРАВЛЕНО #10: Удаляем stop_event из словаря
            _cleanup_stop_event(admin_id)

        if final_progress and admin_id:
            try:
                await bot.send_message(
                    admin_id,
                    texts.BROADCAST_RESULT.format(
                        success_count=final_progress.success_count,
                        fail_count=final_progress.fail_count,
                        label=final_progress.label,
                        total_count=final_progress.total_count,
                    ),
                    reply_markup=get_broadcast_result_keyboard(), parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to send broadcast result to admin {admin_id}: {e}")

            try:
                async with session_scope() as session:
                    await AuditService.log_action(
                        session, admin_id, "BROADCAST",
                        details=(
                            f"to {final_progress.label}: {final_progress.success_count} success, "
                            f"{final_progress.fail_count} fail" if final_progress else "no progress"
                        ),
                    )
            except Exception as e:
                logger.error(f"Failed to log broadcast audit: {e}")

async def resume_pending_broadcasts(bot):
    """
    🔥 ИСПРАВЛЕНО #7: Восстанавливает `_broadcast_in_progress` при рестарте.
    """
    try:
        async with session_scope() as session:
            stmt = select(BroadcastProgress).where(BroadcastProgress.status == "in_progress")
            result = await session.execute(stmt)
            pending = result.scalars().all()
            
            for p in pending:
                if p.admin_id in _broadcast_in_progress:
                    logger.info(
                        f"Broadcast ID {p.id} for admin {p.admin_id} already running, "
                        f"skipping duplicate resume"
                    )
                    continue
                    
                logger.info(f"Resuming interrupted broadcast ID {p.id} for admin {p.admin_id}")
                _broadcast_in_progress.add(p.admin_id)
                asyncio.create_task(_send_broadcast_to_users_with_resume(bot, p.id))
    except Exception as e:
        logger.error(f"Failed to resume broadcasts: {e}", exc_info=True)

async def _start_broadcast_process(callback: CallbackQuery, state: FSMContext, session: AsyncSession, audience: str):
    admin_id = callback.from_user.id
    if admin_id in _broadcast_in_progress:
        await callback.answer("⏳ Рассылка уже идёт, дождитесь завершения", show_alert=True)
        return

    await callback.answer("🚀 Рассылка запущена в фоне")
    
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    if not broadcast_text:
        await callback.answer(texts.ERROR_TEXT_EMPTY, show_alert=True)
        await state.clear()
        return

    media_id = data.get("media_id")
    content_type = data.get("content_type")

    count_stmt = select(func.count(User.id)).where(
        User.is_deleted == False,
        User.is_bot_blocked == False
    )
    if audience == "active":
        current_time = now_utc()
        count_stmt = count_stmt.where(
            User.subscription_end > current_time,
            User.is_banned == False
        )
        
    result = await session.execute(count_stmt)
    total_count = result.scalar_one()

    progress_id = None
    async with session_scope() as sess:
        progress = BroadcastProgress(
            admin_id=admin_id,
            total_count=total_count,
            target_audience=audience,
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
            label="Всего" if audience == "all" else "Активных",
            status="in_progress",
        )
        sess.add(progress)
        await sess.commit()
        await sess.refresh(progress)
        progress_id = progress.id

    _broadcast_in_progress.add(admin_id)
    asyncio.create_task(_send_broadcast_to_users_with_resume(callback.bot, progress_id))

    try:
        await callback.message.edit_text(
            f"🚀 <b>Рассылка запущена!</b>\n"
            f"Отправляю {total_count} пользователям...\n"
            f"Результат придёт отдельным сообщением.",
            reply_markup=get_back_button("admin_menu"), parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(f"edit_text failed in _start_broadcast_process: {e}")
        
    await state.clear()

@router.callback_query(F.data == "broadcast_send_all", AdminStates.confirming_broadcast)
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await _start_broadcast_process(callback, state, session, "all")

@router.callback_query(F.data == "broadcast_send_active", AdminStates.confirming_broadcast)
async def broadcast_to_active(callback: CallbackQuery, state: FSMContext, session: AsyncSession = None):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await _start_broadcast_process(callback, state, session, "active")

@router.callback_query(F.data == "broadcast_stop")
async def stop_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
        
    admin_id = callback.from_user.id
    stop_event = _get_stop_event(admin_id)
    stop_event.set()
    await callback.answer("⏹ Рассылка останавливается...", show_alert=True)

# 🔥 ИСПРАВЛЕНО: Добавлена проверка is_admin для предотвращения несанкционированного удаления сообщений
@router.callback_query(F.data == "broadcast_dismiss")
async def dismiss_broadcast_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass

# 🔥 ИСПРАВЛЕНО: Добавлена проверка is_admin для предотвращения несанкционированного удаления сообщений
@router.callback_query(F.data == "dismiss_broadcast")
async def dismiss_broadcast_message(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(texts.ERROR_ACCESS_DENIED, show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass