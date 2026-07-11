from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from bot.texts import SUPPORT_TEXT
from bot.keyboards import get_back_button
from config.settings import get_settings
from aiogram.utils.keyboard import InlineKeyboardBuilder

router = Router()

FAQ_TEXT = (
    "❓ <b>Частые вопросы</b>\n\n"
    "<b>1. Как подключить устройство?</b>\n"
    "Перейдите в раздел \"🔌 Подключение\" и следуйте инструкциям.\n\n"
    "<b>2. Что делать если не работает подключение?</b>\n"
    "Попробуйте удалить устройство и создать заново. Если не помогло — напишите в поддержку.\n\n"
    "<b>3. Как продлить подписку?</b>\n"
    "Перейдите в раздел \"💳 Оплата\" и выберите подходящий тариф.\n\n"
    "<b>4. Можно ли использовать на нескольких устройствах?</b>\n"
    "Да, лимит устройств указан в вашем профиле (обычно 3).\n\n"
    "<b>5. Как пригласить друга и получить бонус?</b>\n"
    "В разделе \"👤 Профиль\" нажмите \"🎁 Пригласить друга\" и поделитесь ссылкой. За каждого друга вы получите дополнительные дни доступа.\n\n"
    "<b>6. Безопасны ли мои данные?</b>\n"
    "Мы не ведём логи вашей активности. Все подключения зашифрованы современными протоколами."
)


@router.message(F.text == "💬 Поддержка")
async def show_support(message: Message):
    try:
        await message.delete()
    except Exception:
        pass
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    text = SUPPORT_TEXT.format(support_username=f"@{username}")
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery):
    await callback.message.edit_text(
        FAQ_TEXT,
        reply_markup=get_back_button("back_to_support"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_support")
async def back_to_support(callback: CallbackQuery):
    settings = get_settings()
    username = settings.SUPPORT_USERNAME.lstrip('@')
    text = SUPPORT_TEXT.format(support_username=f"@{username}")
    builder = InlineKeyboardBuilder()
    builder.button(text=f"💬 Написать @{username}", url=f"https://t.me/{username}")
    builder.button(text="❓ Частые вопросы", callback_data="faq")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await callback.answer()