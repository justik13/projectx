from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User

from .common import _render_connections

router = Router()


@router.callback_query(F.data == "menu_connections")
async def hub_menu_connections(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_connections(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data == "back_to_connections")
async def back_to_connections(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer()
    await state.clear()

    if not db_user:
        await callback.answer(
            texts.ERROR_USER_NOT_FOUND,
            show_alert=True,
        )
        return

    await _render_connections(
        callback.message,
        db_user,
        session,
    )