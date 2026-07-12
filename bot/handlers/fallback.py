from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from bot import texts

router = Router()

@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
    StateFilter("*"),
)
async def fsm_media_guard(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.ERROR_OPERATION_INTERRUPTED, parse_mode="HTML")

@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
)
async def handle_media(message: Message):
    await message.answer(texts.FALLBACK_MEDIA_TEXT)

@router.message()
async def handle_unknown_text(message: Message):
    if not message.text: return
    if message.text.startswith("/"): return
    await message.answer(texts.FALLBACK_UNKNOWN_TEXT)

@router.callback_query(F.data == "noop_group_header")
async def noop_group_header(callback: CallbackQuery):
    await callback.answer()