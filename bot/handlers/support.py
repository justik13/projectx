from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards import get_back_button
from bot import texts
from config.settings import get_settings

router = Router()

def _support_keyboard(username: str):
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.button(text="← В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1, 1)
    return builder.as_markup()

@router.callback_query(F.data == "menu_support")
async def hub_menu_support(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    username = get_settings().SUPPORT_USERNAME.lstrip("@")
    await callback.message.edit_text(texts.SUPPORT_TEXT.format(support_username=f"@{username}"), reply_markup=_support_keyboard(username), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.edit_text(texts.FAQ_TEXT, reply_markup=get_back_button("menu_support"), parse_mode="HTML")
    await callback.answer()