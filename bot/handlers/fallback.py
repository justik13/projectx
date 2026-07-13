from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from bot import texts
from bot.keyboards import get_back_button
from utils.telegram import render_hub

router = Router()

@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
    StateFilter("*"),
)
async def fsm_media_guard(message: Message, state: FSMContext):
    await state.clear()
    await render_hub(message.bot, message.chat.id, texts.ERROR_OPERATION_INTERRUPTED, get_back_button("back_to_main_menu"))

@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
)
async def handle_media(message: Message):
    await render_hub(message.bot, message.chat.id, texts.FALLBACK_MEDIA_TEXT, get_back_button("back_to_main_menu"))

@router.message()
async def handle_unknown_text(message: Message, state: FSMContext):
    if not message.text: return
    if message.text.startswith("/"): return
    await state.clear()
    await render_hub(message.bot, message.chat.id, texts.FALLBACK_UNKNOWN_TEXT, get_back_button("back_to_main_menu"))

@router.callback_query(F.data == "noop_group_header")
async def noop_group_header(callback: CallbackQuery):
    await callback.answer()

# 🔥 НОВЫЙ ХЕНДЛЕР: Убирает сообщение фонового уведомления
@router.callback_query(F.data == "dismiss_notification")
async def dismiss_notification(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass