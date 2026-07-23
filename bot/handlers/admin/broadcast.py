import asyncio
import logging
import time

from utils.telegram import render_hub, send_hub_photo, safe

from aiogram.filters import StateFilter
from aiogram import Router, F
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button, get_broadcast_confirm_keyboard
from bot.keyboards.admin.broadcast import (
    get_broadcast_close_keyboard,
    get_broadcast_result_keyboard,
)
from bot.states import AdminStates
from database.connection import session_scope
from database.models import BroadcastProgress, User
from database.repositories.users_repo import mark_user_bot_blocked
from services.audit_service import AuditService
from utils.admin import is_admin
from utils.datetime_helpers import now_utc

router = Router()
logger = logging.getLogger(__name__)

_broadcast_stop_events: dict[int, asyncio.Event] = {}
_broadcast_in_progress: set[int] = set()

#
# Храним ссылки на background tasks, чтобы asyncio не собрал их GC.
#
_background_tasks: set[asyncio.Task] = set()


def _start_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


class BroadcastRateLimiter:
    def __init__(self, rate: float = 20.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(
                    self.rate,
                    self.tokens + elapsed * self.rate,
                )
                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


_broadcast_limiter = BroadcastRateLimiter(rate=20.0)


def _get_stop_event(admin_id: int) -> asyncio.Event:
    if admin_id not in _broadcast_stop_events:
        _broadcast_stop_events[admin_id] = asyncio.Event()
    return _broadcast_stop_events[admin_id]


def _cleanup_stop_event(admin_id: int) -> None:
    _broadcast_stop_events.pop(admin_id, None)


@router.callback_query(F.data == "admin_broadcast")
async def start_broadcast(
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

    try:
        await callback.message.edit_text(
            texts.BROADCAST_PROMPT,
            reply_markup=get_back_button("admin_menu"),
        )
    except TelegramBadRequest as e:
        logger.debug(f"start_broadcast edit_text failed: {e}")

    await state.set_state(AdminStates.entering_broadcast_message)


@router.message(AdminStates.entering_broadcast_message)
async def process_broadcast_message(
    message: Message,
    state: FSMContext,
):
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.text and message.text.startswith("/"):
        await state.clear()
        return

    broadcast_text = message.text or message.caption
    if not broadcast_text:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_TEXT_OR_MEDIA,
            get_back_button("admin_menu"),
        )
        return

    media_id = None
    content_type = message.content_type

    if message.photo:
        media_id = message.photo[-1].file_id
    elif message.document:
        media_id = message.document.file_id

    preview = texts.BROADCAST_PREVIEW.format(
        content_type=content_type,
        text=broadcast_text,
    )

    try:
        if media_id and content_type == "photo":
            await send_hub_photo(
                message.bot,
                message.chat.id,
                message.photo[-1],
                caption=preview,
                reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        elif media_id and content_type == "document":
            from utils.telegram import send_hub_document

            await send_hub_document(
                message.bot,
                message.chat.id,
                message.document,
                caption=preview,
                reply_markup=get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )
        else:
            await render_hub(
                message.bot,
                message.chat.id,
                preview,
                get_broadcast_confirm_keyboard(),
                parse_mode="HTML",
            )

        await state.update_data(
            broadcast_text=broadcast_text,
            media_id=media_id,
            content_type=content_type,
        )
        await state.set_state(AdminStates.confirming_broadcast)

    except Exception as e:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_VALIDATION.format(error=safe(str(e))),
            get_back_button("admin_menu"),
        )


async def _send_with_html(
    bot,
    uid,
    text,
    media_id,
    content_type,
    kb,
):
    if content_type == "photo" and media_id:
        await bot.send_photo(
            uid,
            media_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    elif content_type == "document" and media_id:
        await bot.send_document(
            uid,
            media_id,
            caption=text,
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await bot.send_message(
            uid,
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )


async def _send_plain(
    bot,
    uid,
    text,
    media_id,
    content_type,
    kb,
):
    if content_type == "photo" and media_id:
        await bot.send_photo(
            uid,
            media_id,
            caption=text,
            reply_markup=kb,
        )
    elif content_type == "document" and media_id:
        await bot.send_document(
            uid,
            media_id,
            caption=text,
            reply_markup=kb,
        )
    else:
        await bot.send_message(
            uid,
            text,
            reply_markup=kb,
        )


async def _dispatch_message(
    bot,
    uid,
    text,
    media_id,
    content_type,
):
    kb = get_broadcast_close_keyboard()

    try:
        await _send_with_html(
            bot,
            uid,
            text,
            media_id,
            content_type,
            kb,
        )
    except TelegramBadRequest as e:
        if (
            "can't parse entities" in str(e).lower()
            or "parse" in str(e).lower()
        ):
            logger.warning(
                f"HTML parse failed for user {uid}, "
                f"falling back to plain text"
            )
            await _send_plain(
                bot,
                uid,
                text,
                media_id,
                content_type,
                kb,
            )
        else:
            raise


async def _get_next_batch(
    session: AsyncSession,
    audience: str,
    last_id: int,
    limit: int = 50,
):
    """
    Возвращает следующий batch получателей рассылки.

    Важно:
    - используем внутренний User.id как cursor;
    - забаненные пользователи исключаются всегда;
    - удалённые пользователи исключаются всегда;
    - пользователи, заблокировавшие бота, исключаются всегда;
    - для audience=active дополнительно проверяется активная подписка.
    """
    stmt = (
        select(User.id, User.telegram_id)
        .where(
            User.id > last_id,
            User.is_deleted == False,
            User.is_bot_blocked == False,
            User.is_banned == False,
        )
    )

    if audience == "active":
        current_time = now_utc()
        stmt = stmt.where(
            User.subscription_end > current_time,
        )

    stmt = stmt.order_by(User.id).limit(limit)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def _send_broadcast_to_users_with_resume(
    bot,
    progress_id: int,
    admin_id: int,
):
    stop_event = None
    broadcast_text = None
    media_id = None
    content_type = None
    target_audience = None
    last_id = None
    final_progress = None
    should_finalize = False

    try:
        async with session_scope() as session:
            progress = await session.get(BroadcastProgress, progress_id)
            if not progress:
                return

            #
            # Если рассылка уже находится в статусе stopping,
            # например админ нажал stop или бот перезапустился
            # во время остановки, завершаем её как stopped.
            #
            if progress.status == "stopping":
                progress.status = "stopped"
                await session.commit()
                final_progress = progress
                return

            if progress.status != "in_progress":
                return

            should_finalize = True

            stop_event = _get_stop_event(admin_id)
            stop_event.clear()

            broadcast_text = progress.broadcast_text
            media_id = progress.media_id
            content_type = progress.content_type
            target_audience = progress.target_audience
            last_id = progress.last_processed_id

            logger.info(
                f"Broadcast resume/start: admin={admin_id}, "
                f"progress_id={progress_id}, starting from id {last_id}"
            )

        blocked_user_ids = []
        local_success = 0
        local_fail = 0

        while True:
            if stop_event and stop_event.is_set():
                break

            async with session_scope() as session:
                batch = await _get_next_batch(
                    session,
                    target_audience,
                    last_id,
                )

                if not batch:
                    break

                for internal_id, uid in batch:
                    if stop_event and stop_event.is_set():
                        break

                    try:
                        await _broadcast_limiter.acquire()
                        await _dispatch_message(
                            bot,
                            uid,
                            broadcast_text,
                            media_id,
                            content_type,
                        )
                        local_success += 1
                    except TelegramRetryAfter as e:
                        await asyncio.sleep(e.retry_after + 1)
                        try:
                            await _broadcast_limiter.acquire()
                            await _dispatch_message(
                                bot,
                                uid,
                                broadcast_text,
                                media_id,
                                content_type,
                            )
                            local_success += 1
                        except Exception:
                            local_fail += 1
                    except TelegramForbiddenError:
                        blocked_user_ids.append(uid)
                        local_fail += 1
                    except Exception as e:
                        logger.error(
                            f"Broadcast error for user {uid}: {e}"
                        )
                        local_fail += 1

                last_id = internal_id

                progress = await session.get(
                    BroadcastProgress,
                    progress_id,
                )
                if progress:
                    progress.last_processed_id = last_id
                    progress.success_count += local_success
                    progress.fail_count += local_fail
                    await session.commit()

                local_success = 0
                local_fail = 0

        if should_finalize:
            async with session_scope() as session:
                progress = await session.get(
                    BroadcastProgress,
                    progress_id,
                )
                if progress:
                    if (
                        (stop_event and stop_event.is_set())
                        or progress.status == "stopping"
                    ):
                        progress.status = "stopped"
                    else:
                        progress.status = "completed"
                    await session.commit()
                    final_progress = progress

        if blocked_user_ids:
            try:
                async with session_scope() as session:
                    for uid in blocked_user_ids:
                        await mark_user_bot_blocked(session, uid)
            except Exception as e:
                logger.error(
                    f"Failed to batch mark users as bot_blocked: {e}"
                )

    finally:
        if stop_event:
            stop_event.clear()

        _broadcast_in_progress.discard(admin_id)
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
                    reply_markup=get_broadcast_result_keyboard(),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(
                    f"Failed to send broadcast result to admin {admin_id}: {e}"
                )

            try:
                async with session_scope() as session:
                    await AuditService.log_action(
                        session,
                        admin_id,
                        "BROADCAST",
                        details=(
                            f"to {final_progress.label}: "
                            f"{final_progress.success_count} success, "
                            f"{final_progress.fail_count} fail, "
                            f"status={final_progress.status}"
                        ),
                    )
            except Exception as e:
                logger.error(f"Failed to log broadcast audit: {e}")


async def resume_pending_broadcasts(bot):
    """
    Resume рассылки после рестарта.

    Правила:
    - статус stopping считается остановленным и не продолжается;
    - возобновляется только последняя in_progress рассылка для админа;
    - старые дубликаты in_progress помечаются stopped;
    - если для админа уже есть активная задача, дубликаты помечаются stopped.
    """
    try:
        async with session_scope() as session:
            #
            # Если бот перезапустился во время остановки рассылки,
            # статус stopping должен стать stopped.
            #
            await session.execute(
                update(BroadcastProgress)
                .where(BroadcastProgress.status == "stopping")
                .values(status="stopped")
            )

            stmt = (
                select(BroadcastProgress)
                .where(BroadcastProgress.status == "in_progress")
                .order_by(
                    BroadcastProgress.created_at.desc(),
                    BroadcastProgress.id.desc(),
                )
            )
            result = await session.execute(stmt)
            pending = result.scalars().all()

            resume_items: list[tuple[int, int]] = []
            stop_ids: list[int] = []
            seen_admins: set[int] = set()

            for p in pending:
                if p.admin_id in _broadcast_in_progress:
                    stop_ids.append(p.id)
                    continue

                if p.admin_id in seen_admins:
                    stop_ids.append(p.id)
                    continue

                seen_admins.add(p.admin_id)
                resume_items.append((p.id, p.admin_id))

            if stop_ids:
                await session.execute(
                    update(BroadcastProgress)
                    .where(BroadcastProgress.id.in_(stop_ids))
                    .values(status="stopped")
                )
                logger.info(
                    "Marked %s old/duplicate broadcast(s) as stopped",
                    len(stop_ids),
                )

        for progress_id, admin_id in resume_items:
            logger.info(
                f"Resuming interrupted broadcast ID {progress_id} "
                f"for admin {admin_id}"
            )
            _broadcast_in_progress.add(admin_id)
            _start_background_task(
                _send_broadcast_to_users_with_resume(
                    bot,
                    progress_id,
                    admin_id,
                )
            )

    except Exception as e:
        logger.error(
            f"Failed to resume broadcasts: {e}",
            exc_info=True,
        )


async def _start_broadcast_process(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    audience: str,
):
    admin_id = callback.from_user.id

    if admin_id in _broadcast_in_progress:
        await callback.answer(
            texts.BROADCAST_ALREADY_RUNNING,
            show_alert=True,
        )
        return

    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")

    if not broadcast_text:
        await callback.answer(
            texts.ERROR_TEXT_EMPTY,
            show_alert=True,
        )
        await state.clear()
        return

    media_id = data.get("media_id")
    content_type = data.get("content_type")

    count_stmt = (
        select(func.count(User.id))
        .where(
            User.is_deleted == False,
            User.is_bot_blocked == False,
            User.is_banned == False,
        )
    )

    if audience == "active":
        current_time = now_utc()
        count_stmt = count_stmt.where(
            User.subscription_end > current_time,
        )

    result = await session.execute(count_stmt)
    total_count = result.scalar_one()

    if not total_count:
        await callback.answer(
            texts.BROADCAST_NO_RECIPIENTS,
            show_alert=True,
        )
        await state.clear()
        return

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

    _start_background_task(
        _send_broadcast_to_users_with_resume(
            callback.bot,
            progress_id,
            admin_id,
        )
    )

    try:
        await callback.message.edit_text(
            texts.BROADCAST_STARTED.format(total_count=total_count),
            reply_markup=get_back_button("admin_menu"),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"edit_text failed in _start_broadcast_process: {e}"
        )

    await state.clear()


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_all",
)
async def broadcast_to_all(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await _start_broadcast_process(
        callback,
        state,
        session,
        "all",
    )


@router.callback_query(
    StateFilter(AdminStates.confirming_broadcast),
    F.data == "broadcast_send_active",
)
async def broadcast_to_active(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession = None,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await _start_broadcast_process(
        callback,
        state,
        session,
        "active",
    )


@router.callback_query(F.data == "broadcast_stop")
async def stop_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    admin_id = callback.from_user.id

    #
    # Переводим активные рассылки админа в промежуточный статус stopping.
    # Это гарантирует, что после рестарта они не будут возобновлены.
    #
    try:
        async with session_scope() as session:
            await session.execute(
                update(BroadcastProgress)
                .where(
                    BroadcastProgress.admin_id == admin_id,
                    BroadcastProgress.status == "in_progress",
                )
                .values(status="stopping")
            )
    except Exception as e:
        logger.error(
            f"Failed to set broadcast status to stopping: {e}"
        )

    stop_event = _get_stop_event(admin_id)
    stop_event.set()

    await callback.answer(
        texts.BROADCAST_STOPPING,
        show_alert=True,
    )


@router.callback_query(F.data == "broadcast_dismiss")
async def dismiss_broadcast_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer()

    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        logger.debug(
            f"dismiss_broadcast_result delete failed: {e}"
        )


@router.callback_query(F.data == "dismiss_broadcast")
async def dismiss_broadcast_message(callback: CallbackQuery):
    await callback.answer()

    try:
        await callback.message.delete()
    except TelegramBadRequest as e:
        logger.debug(
            f"dismiss_broadcast_message delete failed: {e}"
        )