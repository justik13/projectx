# bot/texts.py — все тексты бота (без слова "VPN"!)

WELCOME_TEXT = (
    "👋 Привет!\n\n"
    "🔐 Подключай устройства к защищённой сети в один клик.\n\n"
    "📋 Ознакомься с пользовательским соглашением, чтобы начать."
)

TOS_TEXT = (
    "📖 Пользовательское соглашение\n"
    "─────────────────────────────\n\n"
    "Используя сервис, ты соглашаешься:\n\n"
    "• Получать доступ к защищённым сетям\n"
    "• Не использовать сервис в противозаконных целях\n"
    "• Администрация может менять условия\n\n"
    "Принимая, ты подтверждаешь согласие с правилами."
)

TOS_ACCEPT_PROMPT = "📋 Прими условия, чтобы продолжить работу с сервисом:"

PROFILE_TEXT = (
    "👤 Твой профиль\n"
    "─────────────────────────────\n"
    "{name} (@{username})\n"
    "id {telegram_id}\n\n"
    "Статус: {status_emoji} {status_text}\n"
    "Действует до: {valid_until}\n"
    "Осталось: {days_left}\n"
    "Устройства: {devices_count}/{device_limit}\n"
    "Всего трафика: {total_traffic}\n\n"
    "🎁 Приглашено: {referrals_count}  ·  Бонус: +{referral_days} дней"
)

REFERRAL_TEXT = (
    "🎁 Пригласи друга — получи {bonus_days} дня бесплатно\n"
    "─────────────────────────────\n\n"
    "🔗 Твоя ссылка:\n"
    "{referral_link}\n\n"
    "[📋 Скопировать]\n\n"
    "Приглашено: {invited_count}\n"
    "Бонус: +{bonus_total} дней"
)

REFERRALS_LIST_HEADER = (
    "👥 Твои рефералы\n"
    "─────────────────────────────\n\n"
)

REFERRAL_ITEM = "{index}. {username} → +{days} дней ✅\n"

CONNECTION_LIST_HEADER = (
    "🔌 Твои устройства ({count}/{limit})\n"
    "─────────────────────────────\n\n"
)

DEVICE_CARD = (
    "📱 {device_name} ({flag} {server_name})\n"
    "{last_connected_text}\n"
    "↓ {traffic_down}  ↑ {traffic_up}  Σ {traffic_total}\n"
)

DEVICE_NOT_CONNECTED = "⏱ Ещё не подключалось"
DEVICE_RECENTLY_ACTIVE = "⏱ Активно недавно ({last_connected})"

SUPPORT_TEXT = (
    "💬 Поддержка\n"
    "─────────────────────────────\n\n"
    "Если есть вопросы — пиши оператору:\n\n"
    "👤 {support_username}\n\n"
    "Отвечаем в течение 24 часов."
)

PAYMENT_TARIFFS_HEADER = (
    "💳 Продление доступа\n"
    "─────────────────────────────\n\n"
    "Выбери тариф:\n\n"
)

PAYMENT_TARIFF_ITEM = "⏱ {days} дней  —  {price_rub} ₽   ·  {price_stars} ⭐\n"

PAYMENT_METHOD_TEXT = (
    "💳 Способ оплаты\n"
    "─────────────────────────────\n\n"
    "Тариф: {duration_days} дней — {price_rub} ₽ / {price_stars} ⭐\n\n"
    "Выбери способ:"
)

PAYMENT_STARS_CONFIRM = (
    "💳 Оплата через Stars\n"
    "─────────────────────────────\n\n"
    "К оплате: {price_stars} ⭐\n\n"
    "После оплаты — доступ активируется сразу."
)

PAYMENT_SUCCESS = (
    "✅ Платёж прошёл!\n"
    "─────────────────────────────\n\n"
    "Доступ продлён на {duration_days} дней.\n"
    "Действует до: {valid_until}"
)
