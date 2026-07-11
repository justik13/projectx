from aiogram import Router, F
from aiogram.fsm.context import FSMContext  # noqa: F401 — сохранено
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import get_back_button
from bot import texts
from config.settings import get_settings
from utils.telegram import safe_delete_message

router = Router()


def _support_keyboard(username: str):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == "💬 Поддержка")
async def show_support(message: Message):
    await safe_delete_message(message)

    username = get_settings().SUPPORT_USERNAME.lstrip("@")

    await message.answer(
        texts.SUPPORT_TEXT.format(support_username=f"@{username}"),
        reply_markup=_support_keyboard(username),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.edit_text(
        texts.FAQ_TEXT,
        reply_markup=get_back_button("back_to_support"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_support")
async def back_to_support(callback: CallbackQuery):
    username = get_settings().SUPPORT_USERNAME.lstrip("@")
    await callback.message.edit_text(
        texts.SUPPORT_TEXT.format(support_username=f"@{username}"),
        reply_markup=_support_keyboard(username),
        parse_mode="HTML",
    )
    await callback.answer()