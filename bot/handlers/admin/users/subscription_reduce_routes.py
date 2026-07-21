import logging
from datetime import timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from bot.keyboards.admin.users import get_admin_confirm_action_keyboard
from bot.middlewares.user_context import invalidate_user_cache
from bot.states import AdminStates
from database.repositories.users_repo import get_user_by_telegram_id
from services.audit_service import AuditService
from services.subscription import SubscriptionService
from utils.admin import is_admin
from utils.formatters import format_datetime
from utils.telegram import render_hub

from .common import _validate_positive_int

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("admin_sub_reduce:"))
async def admin_sub_reduce_start(
    callback: CallbackQuery,
    state: FSMContext,
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

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user or not user.subscription_end:
        await callback.answer(
            "❌ У пользователя нет подписки",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(AdminStates.admin_reducing_days)
    await state.update_data(admin_telegram_id=telegram_id)

    text = texts.ADMIN_SUB_REDUCE_PROMPT.format(
        telegram_id=telegram_id,
        valid_until=format_datetime(user.subscription_end),
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_back_button(
                f"admin_subscription:{telegram_id}"
            ),
            parse_mode="HTML",
        )
    except TelegramBadRequest as e:
        logger.debug(
            f"admin_sub_reduce_start edit_text failed: {e}"
        )


@router.message(AdminStates.admin_reducing_days)
async def admin_sub_reduce_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(message.from_user.id):
        return

    data = await state.get_data()
    telegram_id = data.get("admin_telegram_id")

    if not telegram_id:
        await state.clear()
        return

    days = _validate_positive_int(message.text)

    if days is None:
        await render_hub(
            message.bot,
            message.chat.id,
            texts.ERROR_DAYS_OVERFLOW,
            get_back_button(f"admin_subscription:{telegram_id}"),
            parse_mode="HTML",
        )
        return

    await state.clear()

    user = await get_user_by_telegram_id(session, telegram_id)

    if not user or not user.subscription_end:
        await render_hub(
            message.bot,
            message.chat.id,
            "❌ У пользователя нет активной подписки.",
            get_back_button(f"admin_subscription:{telegram_id}"),
        )
        return

    current_end = user.subscription_end
    new_end = current_end - timedelta(days=days)

    confirm_text = texts.ADMIN_SUB_CONFIRM_REDUCE.format(
        telegram_id=telegram_id,
        current_end=format_datetime(current_end),
        days=days,
        new_end=format_datetime(new_end),
    )

    await render_hub(
        message.bot,
        message.chat.id,
        confirm_text,
        get_admin_confirm_action_keyboard(
            confirm_callback=(
                f"admin_sub_apply_reduce:{telegram_id}:{days}"
            ),
            cancel_callback=(
                f"admin_subscription:{telegram_id}"
            ),
        ),
    )


@router.callback_query(F.data.startswith("admin_sub_apply_reduce:"))
async def admin_sub_apply_reduce(
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

    parts = callback.data.split(":")
    telegram_id = int(parts[1])
    days = int(parts[2])

    try:
        user = await get_user_by_telegram_id(
            session,
            telegram_id,
        )

        if not user or not user.subscription_end:
            await callback.message.edit_text(
                "❌ У пользователя нет подписки."
            )
            return

        new_end = user.subscription_end - timedelta(days=days)

        user.subscription_end = new_end

        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False

        # Сбрасываем флаги grace-уведомлений, чтобы пользователь
        # получил корректные уведомления после изменения подписки.
        user.notified_expired = False
        user.notified_grace_12h = False

        await session.flush()

        # Сразу синхронизируем статус устройств.
        # Если подписка стала истёкшей, устройства должны быть
        # отключены без ожидания traffic worker.
        await SubscriptionService.sync_access_state(session, user)

        invalidate_user_cache(telegram_id)

        await AuditService.log_action(
            session,
            callback.from_user.id,
            "REDUCE",
            "User",
            telegram_id,
            f"-{days} days -> {format_datetime(new_end)}",
        )

        text = texts.ADMIN_SUB_REDUCED.format(
            telegram_id=telegram_id,
            new_end=format_datetime(new_end),
        )

        try:
            await callback.message.edit_text(
                text,
                reply_markup=get_back_button(
                    f"admin_user_card:{telegram_id}"
                ),
                parse_mode="HTML",
            )
        except TelegramBadRequest as e:
            logger.debug(
                f"admin_sub_apply_reduce edit_text failed: {e}"
            )

    except Exception as e:
        logger.error(
            f"admin_sub_apply_reduce error: {e}",
            exc_info=True,
        )
        await session.rollback()

        await callback.answer(
            "❌ Ошибка при уменьшении",
            show_alert=True,
        )