from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from bot.texts import FALLBACK_MEDIA_TEXT, FALLBACK_UNKNOWN_TEXT

router = Router()


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note |
    F.document | F.audio | F.location | F.contact | F.poll | F.dice | F.animation,
    StateFilter("*")
)
async def fsm_media_guard(message: Message, state: FSMContext):
    """🔥 НОВОЕ: защита от стикеров/фото во время активного FSM"""
    await state.clear()
    await message.answer(
        "⚠️ <b>Операция прервана.</b>\n"
        "Я ожидаю текстовый ввод или нажатие кнопок.\n"
        "Пожалуйста, используйте меню внизу экрана.",
        parse_mode="HTML"
    )


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note |
    F.document | F.audio | F.location | F.contact | F.poll | F.dice | F.animation
)
async def handle_media(message: Message):
    """Обработчик для всех медиа-сообщений (стикеры, кружочки, фото, голосовые)."""
    await message.answer(FALLBACK_MEDIA_TEXT)


@router.message()
async def handle_unknown_text(message: Message):
    """Обработчик для любых текстовых сообщений, которые не распознаны."""
    if message.text and message.text.startswith("/"):
        return
    main_menu_buttons = [
        "👤 Профиль", "🔌 Подключение", "💳 Оплата", "💬 Поддержка", "🛠 Админка"
    ]
    if message.text in main_menu_buttons:
        return
    await message.answer(FALLBACK_UNKNOWN_TEXT)
