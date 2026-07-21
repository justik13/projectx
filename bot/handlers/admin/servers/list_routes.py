from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from utils.admin import is_admin

from .common import _show_servers_list

router = Router()


@router.callback_query(F.data == "admin_servers")
async def show_servers_list(
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

    await state.clear()
    await _show_servers_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_servers_page:"))
async def servers_pagination(
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

    await state.clear()

    page = int(callback.data.split(":")[1])
    await _show_servers_list(callback, session, page=page)