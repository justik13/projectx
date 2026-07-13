WELCOME_TEXT = (
    "👋 <b>Добро пожаловать!</b>\n"
    "🔐 Здесь вы можете подключить свои устройства к ProjectX в один клик.\n"
    "ℹ️ <i>Используя сервис, вы автоматически соглашаетесь с условиями ниже.</i>"
)

HUB_HEADER = (
    "🏠 <b>Главное меню</b>\n"
    "👋 Привет, <b>{name}</b>!\n"
    "Выберите нужный раздел:\n"
    "<i>Используя бота, вы автоматически соглашаетесь с Условиями сервиса и Политикой конфиденциальности. Читать: 💬 Поддержка.</i>"
)

PROFILE_TEXT_ACTIVE = (
    "👤 <b>Профиль</b>\n"
    "{name} (@{username})\n"
    "ID: <code>{telegram_id}</code>\n"
    "💎 <b>Тариф:</b> {tariff_name}\n"
    "🔌 <b>Устройства:</b> {devices_count}\n"
    "📊 <b>Всего трафика:</b> {total_traffic}\n"
    "🎁 <b>Рефералов:</b> {referrals_count}  ·  Получено дней: +{referral_days}"
)

PROFILE_TEXT_INACTIVE = (
    "👤 <b>Профиль</b>\n"
    "{name} (@{username})\n"
    "ID: <code>{telegram_id}</code>\n"
    "🔴 <b>Статус:</b> Нет активной подписки\n"
    "Чтобы подключать устройства и пользоваться сервисом, оформите подписку.\n"
    "🎁 <b>Рефералов:</b> {referrals_count}  ·  Получено дней: +{referral_days}"
)

REFERRAL_TEXT = (
    "🎁 <b>Пригласи друга — получи {bonus_days} дня бесплатно</b>\n"
    "🔗 <b>Ваша ссылка:</b>\n"
    "<code>{referral_link}</code>\n"
    "💡 <i>Нажмите на ссылку выше, чтобы скопировать, или используйте кнопку ниже.</i>\n"
    "Приглашено: {invited_count}\n"
    "Получено дней: +{bonus_total}"
)

REFERRAL_LIST_HEADER = "👥 <b>Ваши рефералы</b>\n"
REFERRAL_LIST_EMPTY = "<i>Список рефералов пока пуст.</i>\nПригласите друзей по вашей ссылке, чтобы они появились здесь."
REFERRAL_LIST_FOOTER = "\nВсего приглашено: {count} пользователей"

CONNECTION_LIST_HEADER = "🔌 <b>Ваши устройства</b> ({count}/{limit})\n"
CONNECTION_EMPTY = "<i>У вас пока нет подключённых устройств.</i>"
CONNECTION_SELECT_SERVER = "🌍 <b>Выберите локацию для подключения:</b>\n"

DEVICE_CARD = (
    "📱 <b>{device_name}</b> ({flag} {server_name})\n"
    "{last_connected_text}\n"
    "↓ {traffic_down}  ↑ {traffic_up}  Σ {traffic_total}\n"
)
DEVICE_NOT_CONNECTED = "⏱ Ещё не подключалось"
DEVICE_RECENTLY_ACTIVE = "⏱ Активно недавно ({last_connected})"

DEVICE_MANAGE_HEADER = (
    "📱 <b>Управление устройством</b>\n"
    "<b>{device_name}</b>\n"
    "📍 Локация: {flag} {server_name}\n"
    "📡 Протокол: {protocol}\n"
    "📊 Трафик: ∑ {traffic_total}\n"
    "⏱ Последняя активность: {last_connected}\n"
    "<i>Нажмите «🔑 Показать ключ», чтобы получить ключ подключения.</i>"
)

DEVICE_SHOW_KEY = (
    "🔑 <b>Ключ подключения для {device_name}:</b>\n"
    "<code>{raw_config}</code>\n"
    "<i>💡 Нажмите на моноширинный текст выше, чтобы скопировать ключ.</i>"
)

DEVICE_CONF_CAPTION = (
    "📁 <b>Конфигурация {protocol_badge}</b>\n"
    "📱 Устройство: <b>{device_name}</b>\n"
    "<i>Импортируйте файл в приложение для подключения.</i>"
)

DEVICE_DELETE_CONFIRM = (
    "⚠️ <b>Подтверждение удаления</b>\n"
    "Вы уверены, что хотите удалить устройство:\n"
    "📱 <b>{device_name}</b>?\n"
    "<i>После удаления вам нужно будет создать устройство заново.</i>"
)

DEVICE_ADD_NAME_PROMPT = (
    "✏️ <b>Введите имя устройства для {flag} {server_name}:</b>\n"
    "(например: IPhone, MacBook, Work PC)\n"
    "Максимум 16 символов, только латиница и цифры."
)

DEVICE_ADDED_SUCCESS = (
    "✅ <b>Устройство добавлено!</b>\n"
    "📱 {device_name} ({flag} {server_name})\n"
    "<i>Используйте кнопки ниже, чтобы получить ключ подключения.</i>"
)

DEVICE_RENAME_PROMPT = "✏️ <b>Переименование устройства</b>\nВведите новое имя (латиница, цифры, пробелы, дефисы, до 16 символов):"

SUPPORT_TEXT = "💬 <b>Поддержка</b>\nЕсли у вас возникли вопросы, напишите нашему оператору:\n👤 {support_username}\nМы отвечаем в течение 24 часов."

FAQ_TEXT = (
    "❓ <b>Частые вопросы</b>\n"
    "<b>1. Как подключить устройство?</b>\n"
    "Перейдите в раздел «🔌 Подключения» и следуйте инструкциям.\n"
    "<b>2. Что делать если не работает подключение?</b>\n"
    "Попробуйте удалить устройство и создать заново. Если не помогло — напишите в поддержку.\n"
    "<b>3. Как продлить подписку?</b>\n"
    "Нажмите «⏳ Моя подписка» в главном меню.\n"
    "<b>4. Можно ли использовать на нескольких устройствах?</b>\n"
    "Да, лимит устройств указан в вашем тарифе.\n"
    "<b>5. Как пригласить друга и получить бонус?</b>\n"
    "В разделе «👤 Профиль» нажмите «🎁 Пригласить друга» и поделитесь ссылкой.\n"
    "<b>6. Безопасны ли мои данные?</b>\n"
    "Мы не ведём логи вашей активности. Все подключения используют современные протоколы шифрования."
)

PAYMENT_SHOWCASE_HEADER = "🛡 <b>Выберите формат подписки</b>\nВыберите тариф, который подходит под ваши задачи. Все серверы работают на максимальной скорости.\n"

PAYMENT_HUB_HEADER = (
    "⏳ <b>Ваша подписка</b>\n"
    "🟢 <b>Статус:</b> Активна\n"
    "📅 <b>Действует до:</b> {valid_until} <i>(осталось {days_left})</i>\n"
    "💎 <b>Тариф:</b> {tariff_name}\n"
    "🔌 <b>Устройства:</b> {devices_count} / {device_limit}\n"
    "Выберите действие:"
)

PAYMENT_QUICK_RENEW_HEADER = "🔄 <b>Продление доступа</b>\nВаш текущий тариф: <b>{tariff_name}</b>\nАктивен до: <i>{valid_until}</i>\nВыберите, на сколько продлить:"

PAYMENT_CHANGE_TARIFF_HEADER = (
    "⚙️ <b>Смена тарифа</b>\n"
    "Сейчас у вас: <b>{tariff_name}</b> (до {valid_until})\n"
    "⚠️ <b>Важно:</b>\n"
    "🔼 <b>Апгрейд</b> (больше устройств) — применится мгновенно.\n"
    "🔽 <b>Даунгрейд</b> (меньше устройств) — <b>недоступен</b> во время активной подписки, чтобы мы случайно не отключили ваши устройства.\n"
    "Выберите новый тариф:"
)

# 🔥 УНИФИЦИРОВАНО: "📱 Базовый" везде, убран бизнес-тариф
PAYMENT_TARIFF_DESCRIPTION = {
    2: (
        "📱 <b>Базовый</b> (до 2 устройств)\n"
        "<i>Телефон и ноутбук. Отличный старт.</i>\n"
    ),
    5: (
        "👨‍👩‍👧‍👦 <b>Семейный</b> (до 5 устройств)\n"
        "<i>Подключите всю семью. Самый популярный!</i> 🏆\n"
    ),
    10: (
        "🚀 <b>Pro</b> (до 10 устройств)\n"
        "<i>Для офиса или большого парка гаджетов.</i>\n"
    ),
}

PAYMENT_DURATION_HEADER = "⏱ <b>На какой срок открываем доступ?</b>\n"

PAYMENT_CHECKOUT_TEXT = "💳 <b>Оформление заказа</b>\n📦 Тариф: <b>{tariff_name}</b>\n⏱ Срок: {duration_days} дней\n💰 Итого: <b>{price_rub} ₽</b> / {price_stars} ⭐\nВыберите удобный способ оплаты:"

PAYMENT_SBP_TEXT = "💳 <b>Оплата через СБП</b>\nК оплате: <b>{price_rub} ₽</b>\nНажмите кнопку ниже для оплаты — доступ будет активирован мгновенно."

PAYMENT_DOWNGRADE_BLOCKED = "⚠️ <b>Переход на тариф с меньшим лимитом</b>\nСейчас у вас активна подписка с лимитом <b>{current_limit}</b> устройств.\nВыбранный тариф поддерживает только <b>{new_limit}</b>.\nЧтобы не прерывать работу ваших гаджетов, переход на этот тариф возможен <b>только после окончания текущей подписки</b> ({valid_until}).\nПожалуйста, выберите тариф с таким же или большим лимитом устройств."

PAYMENT_SUCCESS_NEW = "🎉 <b>Добро пожаловать!</b>\n✅ Оплата прошла успешно.\n💎 Ваш тариф: <b>{tariff_name}</b>\n📅 Действует до: <b>{valid_until}</b>\nГотовы начать?"

PAYMENT_SUCCESS_RENEW = "✅ <b>Доступ успешно продлен!</b>\n💎 Тариф: <b>{tariff_name}</b>\n📅 Действует до: <b>{valid_until}</b>\nСпасибо, что остаетесь с нами!"

PAYMENT_NO_TARIFFS = "💳 В данный момент нет доступных тарифов.\nОбратитесь в поддержку для оформления подписки вручную."

PAYMENT_DELAYED = "⚠️ Возникла задержка при зачислении. Пожалуйста, напишите в поддержку."

HISTORY_HEADER = "🧾 <b>История оплат</b>\n"
HISTORY_EMPTY = "<i>История пуста. У вас пока не было оплат.</i>"
HISTORY_LIMIT_NOTE = "\n<i>Показаны последние 10 из {count} оплат</i>"

FALLBACK_MEDIA_TEXT = "🤖 Я текстовый ассистент и пока не умею распознавать картинки, голосовые сообщения, стикеры или видео-кружочки.\nПожалуйста, используйте кнопки в меню или вернитесь в главное меню."

FALLBACK_UNKNOWN_TEXT = "🤔 Я не понимаю эту команду.\nПожалуйста, используйте кнопки в меню или вернитесь в главное меню."

TOS_AGREEMENT_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-04-01-19"
PRIVACY_POLICY_URL = "https://telegra.ph/Politika-konfidencialnosti-04-01-26"

DOWNLOAD_CONF_FALLBACK = (
    "⚠️ <b>Не удалось автоматически собрать .conf файл</b> для устройства <b>{device_name}</b>.\n"
    "Пожалуйста, используйте кнопку «🔑 Показать ключ» для ручного импорта в приложение "
    "или обратитесь в <b>💬 Поддержку</b>, мы поможем настроить подключение."
)

ERROR_ACCESS_DENIED = "⛔️ Нет доступа"
ERROR_ACCESS_PANEL = "⛔️ У вас нет доступа к админ-панели."
ERROR_BANNED_MESSAGE = "⛔️ У вас заблокирован доступ к сервису.\nЕсли вы считаете, что это ошибка, свяжитесь с поддержкой."
ERROR_BANNED_ALERT = "⛔️ У вас заблокирован доступ к сервису."
ERROR_ADMIN_BAN_FORBIDDEN = "⛔️ Нельзя банить администраторов"
ERROR_TEXT_REQUIRED = "⚠️ Пожалуйста, отправьте текстовое сообщение."
ERROR_TEXT_EXPECTED = "⚠️ Ожидается текстовый ввод."
ERROR_TEXT_OR_MEDIA = "⚠️ Отправьте текст или фото/документ с описанием."
ERROR_NUMERIC_ID = "⚠️ Отправьте числовой Telegram ID:"
ERROR_NUMBER_GT_ZERO = "⚠️ Введите число больше 0:"
ERROR_POSITIVE_NUMBER = "⚠️ Введите положительное число:"
ERROR_NAME_TOO_LONG = "⚠️ Слишком длинное имя (макс. {max} символов)."
ERROR_URL_TOO_LONG = "⚠️ Слишком длинный URL (макс. {max} символов)."
ERROR_API_KEY_SHORT = "⚠️ API ключ слишком короткий (минимум {min} символов)."
ERROR_DAYS_RANGE = "⚠️ Введите число от {min} до {max}:"
ERROR_STARS_POSITIVE = "⚠️ Введите число больше 0 (Stars требует положительную сумму):"
ERROR_INVALID_URL = "⚠️ Некорректный формат URL.\nURL должен начинаться с <code>http://</code> или <code>https://</code>\nПример: <code>http://127.0.0.1:4001</code>"
ERROR_INVALID_DEVICE_NAME = "⚠️ Имя устройства должно быть от 1 до 16 символов (латиница, цифры, пробелы, дефисы):"
ERROR_VALIDATION = "❌ Ошибка валидации: {error}\nВведите заново:"
ERROR_OPERATION_CANCELLED = "⚠️ Операция прервана."
ERROR_OPERATION_INTERRUPTED = "⚠️ <b>Операция прервана.</b>\nЯ ожидаю текстовый ввод или нажатие кнопок.\nПожалуйста, используйте меню или вернитесь назад."
ERROR_USER_NOT_FOUND = "❌ Пользователь не найден"
ERROR_SERVER_NOT_FOUND = "❌ Сервер не найден"
ERROR_TARIFF_NOT_FOUND = "❌ Тариф не найден"
ERROR_PROFILE_NOT_FOUND = "❌ Профиль не найден"
ERROR_DEVICE_NOT_FOUND = "❌ Устройство не найдено"
ERROR_LOCATION_NOT_FOUND = "❌ Локация не найдена"
ERROR_TARIFF_UNAVAILABLE = "❌ Выбранный тариф сейчас недоступен"
ERROR_PAYMENT_DATA_INVALID = "❌ Ошибка данных"
ERROR_TARIFF_INVALID_PRICE = "❌ Ошибка тарифа: некорректная цена."
ERROR_TEXT_EMPTY = "❌ Текст сообщения пуст"
ERROR_TECHNICAL_MESSAGE = "⚠️ <b>Ошибка сервера</b>\nМы уже чиним проблему. Попробуйте позже."
ERROR_TECHNICAL_ALERT = "⚠️ Ведутся технические работы. Попробуйте через минуту."
ERROR_TOO_FREQUENT = "⏳ Слишком часто!"
ERROR_SERVER_UNAVAILABLE_GENERIC = "⚠️ Сервер недоступен. Попробуйте позже."
ERROR_SERVER_UNREACHABLE = "❌ <b>Сервер недоступен!</b>\nНе удалось подключиться к API по указанному адресу.\nВозможные причины:\n• Неверный URL или API ключ\n• Сервер выключен или недоступен\n• Файрвол блокирует соединение\n• API-сервис не запущен\nПроверьте данные и попробуйте снова."
ERROR_SERVER_API_INFO_FAILED = "❌ <b>Ошибка подключения к API!</b>\nСервер отвечает на healthcheck, но не удалось получить информацию.\nВозможно, неверный API ключ."
ERROR_PROTOCOL_NOT_SUPPORTED = "⚠️ <b>Протокол amneziawg2 не поддерживается!</b>\nДоступные протоколы на сервере: <code>{protocols}</code>\nЭтот бот работает только с протоколом <b>amneziawg2</b>."
ADMIN_TOGGLE_NETWORK_FAIL = "⚠️ API недоступен. Статус сервера не изменён."
ADMIN_DELETE_SERVER_NETWORK_FAIL = "⚠️ Ошибка сети: не удалось отключить устройства на сервере. БД не изменена."
ERROR_NO_SUBSCRIPTION = "⚠️ <b>У вас нет активной подписки.</b>\nПродлите доступ, чтобы подключать устройства."
ERROR_DEVICE_LIMIT_REACHED = "⚠️ <b>Достигнут лимит устройств</b> ({limit}).\nУдалите одно из устройств или перейдите на тариф с большим лимитом."
ERROR_SERVER_UNAVAILABLE = "⚠️ <b>Выбранный сервер временно недоступен.</b>\nПопробуйте другую локацию или обратитесь в поддержку."
ERROR_NO_FREE_SLOTS = "❌ На всех серверах закончились свободные слоты."
ERROR_PAYMENT_SERVICE = "❌ Ошибка платежной системы Telegram. Попробуйте позже."
ADMIN_SERVER_CHECKING = "🔍 <b>Проверяю доступность сервера...</b>\nОжидайте, это может занять несколько секунд."
ADMIN_SERVER_ADDED = "✅ <b>Сервер добавлен и проверен!</b>\n{flag} <b>{name}</b>\nПротокол: {protocol}\nМакс клиентов (из API): {max_clients}\nAPI: <code>{api_url}</code>"
ADMIN_SERVER_CARD = "🛠 Админка › 🌍 Серверы › {flag} <b>{name}</b>\n<b>ID:</b> {id}\n<b>Статус:</b> {status}\n<b>Протокол:</b> {protocol}\n<b>API URL:</b> {api_url}\n<b>Макс клиентов:</b> {max_clients}"
ADMIN_SERVER_NAME_PROMPT = "🛠 Админка › 🌍 Серверы › ➕ <b>Новый сервер</b>\n✏️ Введите имя сервера (например: Нидерланды):"
ADMIN_SERVER_FLAG_PROMPT = "🏳️ Введите флаг страны (эмодзи, например: 🇳🇱):"
ADMIN_SERVER_URL_PROMPT = "🔗 Введите API URL сервера (например: http://127.0.0.1:4001):"
ADMIN_SERVER_KEY_PROMPT = "🔑 Введите API ключ сервера:"
ADMIN_SERVER_RENAME_PROMPT = "🛠 Админка › 🌍 Серверы › ✏️ <b>Редактирование</b>\n✏️ Введите новое имя сервера:"
ADMIN_SERVER_RENAMED = "✅ Имя сервера изменено на: {name}"
ADMIN_TARIFF_CARD = "🛠 Админка › 💰 Тарифы › <b>Тариф</b>\n<b>ID:</b> {id}\n<b>Дней:</b> {duration_days}\n<b>Устройств:</b> {device_limit}\n<b>Цена ₽:</b> {price_rub}\n<b>Цена ⭐:</b> {price_stars}\n<b>Статус:</b> {status}"
ADMIN_TARIFF_EDIT_DAYS_PROMPT = "🛠 Админка › 💰 Тарифы › ⏱ <b>Изменить дни</b>\n⏱ Введите новое количество дней:"
ADMIN_TARIFF_EDIT_DEVICES_PROMPT = "🛠 Админка › 💰 Тарифы › 📱 <b>Изменить лимит устройств</b>\n📱 Введите новый лимит устройств (число ≥ 1):"
ADMIN_TARIFF_EDIT_RUB_PROMPT = "🛠 Админка › 💰 Тарифы › 💵 <b>Изменить цену ₽</b>\n💵 Введите новую цену в рублях:"
ADMIN_TARIFF_EDIT_STARS_PROMPT = "🛠 Админка › 💰 Тарифы › ⭐ <b>Изменить цену Stars</b>\n⭐ Введите новую цену в Stars:"
ADMIN_USERS_HEADER = "🛠 Админка › 👥 <b>Пользователи</b>\n(стр. {page}/{total_pages}) · Всего: {total}\n"
ADMIN_USERS_EMPTY = "<i>Пользователей пока нет</i>\n"
ADMIN_USER_SEARCH_PROMPT = "🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>\nВведите Telegram ID пользователя:"
ADMIN_USER_CARD = "🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>\n<b>ID:</b> <code>{telegram_id}</code>\n<b>Username:</b> @{username}\n<b>Имя:</b> {first_name}\n<b>Статус:</b> {status}\n<b>Бан:</b> {ban}\n<b>Действует до:</b> {valid_until}\n<b>Осталось:</b> {days_left}\n<b>Устройств:</b> {devices_count}/{device_limit}\n<b>Рефералов:</b> {referrals_count}\n<b>Бонусных дней:</b> +{referral_days}\n<b>Регистрация:</b> {created_at}"
ADMIN_EXTEND_HEADER = "🛠 Админка › 👥 Пользователи › ⏰ <b>Продление доступа</b>\nВыберите срок продления для <code>{telegram_id}</code>:"
ADMIN_CUSTOM_EXTEND_HEADER = "🛠 Админка › 👥 Пользователи › ⌨️ <b>Ручное продление</b>\nВведите количество дней для продления <code>{telegram_id}</code>:"
ADMIN_EXTENDED_CUSTOM = "✅ Подписка пользователя <code>{telegram_id}</code> продлена на {days} дней.\nДействует до: {valid_until}"
ADMIN_USER_DEVICES_HEADER = "🛠 Админка › 👥 Пользователи › 🔧 <b>Устройства</b>\nПользователь <code>{telegram_id}</code>\n"
ADMIN_USER_DEVICES_EMPTY = "<i>Устройств нет</i>\n"
AUDIT_LOG_HEADER = "🛠 Админка › 📜 <b>Аудит-лог</b>\n<i>Последние 10 действий администраторов:</i>\n"
AUDIT_LOG_EMPTY = "<i>Лог действий пуст.</i>"
AUDIT_ENTRY = "[{date}]\nAdmin <code>{admin_id}</code>\n➡️ {action}{target}{details}\n"
AUDIT_ACTIONS = {"EXTEND": "⏰ Продлил", "BAN": "🚫 Забанил", "UNBAN": "✅ Разбанил", "DELETE_SERVER": "🗑 Удалил сервер", "ADD_SERVER": "➕ Добавил сервер", "TOGGLE_SERVER": "🔄 Переключил сервер", "DELETE_TARIFF": "🗑 Удалил тариф", "ADD_TARIFF": "➕ Добавил тариф", "EDIT_TARIFF": "✏️ Изменил тариф", "BROADCAST": "📢 Сделал рассылку"}
DASHBOARD_HEADER = "🛠 <b>Админ-панель</b>\n📊 <b>Статистика:</b>\n"
DASHBOARD_STATS = "👥 Всего пользователей: {total_users}\n✅ Активных подписок: {active_subs}\n🆕 Новых за 24ч: {new_users_24h}\n🌍 Свободных IP: {free_ips}\n"
BROADCAST_PROMPT = "🛠 Админка › 📢 <b>Рассылка</b>\n📢 Введите текст сообщения для рассылки:\nПоддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)"
BROADCAST_PREVIEW = "📢 <b>Предпросмотр рассылки ({content_type}):</b>\n{text}"
BROADCAST_RESULT = "✅ Рассылка завершена!\n📤 Отправлено: {success_count}\n❌ Ошибок: {fail_count}\n👥 {label}: {total_count}"
ERROR_TARIFF_IN_USE = "⚠️ <b>Удаление тарифа заблокировано</b>\nЭтот тариф сейчас используют <b>{user_count}</b> активных клиентов.\nЧтобы удалить тариф, сначала переведите пользователей на другой тариф или дождитесь окончания их подписок."
TARIFF_DELETED_SUCCESS = "✅ <b>Тариф успешно удалён</b>\nТариф <b>ID:{tariff_id}</b> ({duration_days} дн., {device_limit} устр.) полностью удалён из системы."
ADMIN_SERVER_DELETE_CONFIRM = "⚠️ <b>Подтверждение удаления сервера</b>\n{flag} <b>{name}</b>\nНа этом сервере находится <b>{profiles_count}</b> активных устройств.\nЧто произойдёт:\n• Все устройства будут удалены с сервера (API DELETE)\n• Профили будут удалены из локальной БД\n• Сам сервер будет удалён из системы\n<i>Это действие необратимо.</i>"
ADMIN_SERVER_DELETED = "✅ <b>Сервер полностью удалён</b>\n{flag} {name}\nУдалено устройств: {profiles_count}"
ADMIN_SERVER_FLAG_PROMPT_EDIT = "🛠 Админка › 🌍 Серверы › 🏳 <b>Изменить флаг</b>\nТекущий флаг: {current_flag}\nВведите новый флаг страны (эмодзи, например: 🇩🇪):"
ADMIN_SERVER_FLAG_UPDATED = "✅ Флаг сервера изменён на: {flag}"
ERROR_SERVER_DUPLICATE_URL = "⚠️ <b>Сервер с таким API URL уже существует!</b>\nURL: <code>{api_url}</code>\nНельзя добавить один и тот же сервер дважды."