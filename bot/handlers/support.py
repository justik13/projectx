from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from bot.texts import SUPPORT_TEXT
from bot.keyboards import get_support_keyboard, get_back_button
from config.settings import get_settings
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()

FAQ_TEXT = """❓ Частые вопросы
─────────────────────────────

<b>1. Как подключить устройство?</b>
Перейдите в раздел "🔌 Подключение" и следуйте инструкциям.

<b>2. Что делать если не работает подключение?</b>
Попробуйте удалить устройство и создать заново. Если не помогло — напишите в поддержку.

<b>3. Как продлить подписку?</b>
Перейдите в раздел "💳 Оплата" и выберите подходящий тариф.

<b>4. Можно ли использовать на нескольких устройствах?</b>
Да, лимит устройств указан в вашем профиле (обычно 3).

<b>5. Как пригласить друга и получить бонус?</b>
В разделе "👤 Профиль" нажмите "🎁 Пригласить друга" и поделитесь ссылкой. За каждого друга вы получите дополнительные дни доступа.

<b>6. Безопасны ли мои данные?</b>
Мы не ведём логи вашей активности. Все подключения зашифрованы современными протоколами.
"""


@router.message(F.text == "💬 Поддержка")
async def show_support(message: Message):
    """Показать раздел поддержки"""
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    text = SUPPORT_TEXT.format(support_username=f"@{username}")
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.adjust(1)
    
    await message.answer(
        text,
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    """Показать FAQ"""
    await callback.message.edit_text(
        FAQ_TEXT,
        reply_markup=get_back_button("back_to_support"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_support")
async def back_to_support(callback: CallbackQuery):
    """Вернуться в поддержку"""
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    text = SUPPORT_TEXT.format(support_username=f"@{username}")
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.adjust(1)
    
    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()