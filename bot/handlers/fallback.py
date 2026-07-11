from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.constants import REPLY_MENU_BUTTONS
from bot import texts

router = Router()


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
    StateFilter("*"),
)
async def fsm_media_guard(message: Message, state: FSMContext):
    """Защита от медиа во время активного FSM — сброс состояния."""
    await state.clear()
    await message.answer(texts.ERROR_OPERATION_INTERRUPTED, parse_mode="HTML")


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
)
async def handle_media(message: Message):
    """Обработчик всех медиа вне FSM."""
    await message.answer(texts.FALLBACK_MEDIA_TEXT)


@router.message()
async def handle_unknown_text(message: Message):
    """Обработчик нераспознанного текста."""
    if not message.text:
        return
    if message.text.startswith("/"):
        return
    if message.text in REPLY_MENU_BUTTONS:
        return

    await message.answer(texts.FALLBACK_UNKNOWN_TEXT)

@router.callback_query(F.data == "noop_group_header")
async def noop_group_header(callback: CallbackQuery):
    """Некликабельный заголовок группы тарифов."""
    await callback.answer()