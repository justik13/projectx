from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from utils.telegram import render_hub

router = Router()


def _support_keyboard(username: str):
    builder = InlineKeyboardBuilder()

    builder.button(
        text=f"💬 Написать @{username}",
        url=f"https://t.me/{username}",
    )

    builder.button(
        text="❓ Частые вопросы",
        callback_data="faq",
    )

    builder.button(
        text="📄 Условия сервиса",
        url=texts.TOS_AGREEMENT_URL,
    )

    builder.button(
        text="🔒 Политика",
        url=texts.PRIVACY_POLICY_URL,
    )

    builder.button(
        text="← В главное меню",
        callback_data="back_to_main_menu",
    )

    builder.adjust(1, 1, 2, 1)

    return builder.as_markup()


@router.callback_query(F.data == "menu_support")
async def hub_menu_support(
    callback: CallbackQuery,
    state: FSMContext,
):
    await callback.answer()
    await state.clear()

    username = get_settings().SUPPORT_USERNAME.lstrip("@")

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.SUPPORT_TEXT.format(support_username=f"@{username}"),
        _support_keyboard(username),
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.answer()

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        texts.FAQ_TEXT,
        get_back_button("menu_support"),
    )