# bot/texts_data/admin_texts.py
#
# Админские и сервисные тексты.
#
# Здесь хранятся:
# - админ-панель;
# - серверы;
# - тарифы;
# - пользователи в админке;
# - broadcast;
# - audit;
# - maintenance;
# - алерты админам;
# - тексты фоновых воркеров.
#
# Правила:
# 1. Ключи должны быть UNIQUE.
# 2. Ключи должны быть валидными Python identifier.
# 3. Нельзя дублировать ключи из user_texts.py.
# 4. Все тексты должны быть только здесь.

TEXTS = {
    # ============================================================
    # ADMIN COMMON
    # ============================================================
    "ERROR_ACCESS_PANEL": "⛔️ У вас нет доступа к админ-панели.",
    "ERROR_ADMIN_BAN_FORBIDDEN": "⛔️ Нельзя банить администраторов",

    # ============================================================
    # DASHBOARD
    # ============================================================
    "DASHBOARD_HEADER": """🛠 <b>Админ-панель</b>

📊 <b>Статистика:</b>
""",
    "DASHBOARD_STATS": """👥 Всего пользователей: {total_users}
✅ Активных подписок: {active_subs}
🆕 Новых за 24ч: {new_users_24h}
🌍 Свободных IP: {free_ips}
""",

    # ═══ НОВОЕ (Спринт 1+3): тексты maintenance для дашборда ═══
    "DASHBOARD_MAINTENANCE_ON": "\n🛠 <b>Технические работы:</b> 🔴 ВКЛЮЧЕНЫ\n<i>Новые подключения и оплата временно ограничены.</i>\n",
    "DASHBOARD_MAINTENANCE_OFF": "\n🛠 <b>Технические работы:</b> 🟢 выключены\n",

    # ============================================================
    # MAINTENANCE ADMIN
    # ============================================================
    "ADMIN_MAINTENANCE_MENU_ENABLED": """🛠 <b>Режим технических работ</b>
━━━━━━━━━━━━━━━━━━━━

Текущий статус: 🔴 <b>ВКЛЮЧЕН</b>

Что сейчас ограничено:
• создание новых устройств;
• создание новых платежей;
• выбор тарифа.

Что продолжает работать:
• существующие подключения;
• админ-панель;
• поддержка;
• обработка уже оплаченных платежей.

⚠️ <b>Выключить режим технических работ?</b>
Все ограничения для пользователей будут сняты.""",

    "ADMIN_MAINTENANCE_MENU_DISABLED": """🛠 <b>Режим технических работ</b>
━━━━━━━━━━━━━━━━━━━━

Текущий статус: 🟢 <b>ВЫКЛЮЧЕН</b>

⚠️ <b>Включить режим технических работ?</b>

Что будет ограничено:
• создание новых устройств;
• создание новых платежей;
• выбор тарифа.

Что продолжит работать:
• существующие подключения;
• админ-панель;
• поддержка;
• обработка уже оплаченных платежей.

<i>Администраторы могут обходить этот режим.</i>""",

    "ADMIN_MAINTENANCE_ENABLED_ANSWER": "✅ Технические работы включены",
    "ADMIN_MAINTENANCE_DISABLED_ANSWER": "✅ Технические работы выключены",
    "ADMIN_MAINTENANCE_TOGGLE_FAILED": "❌ Не удалось изменить режим технических работ",

    # ============================================================
    # AUDIT
    # ============================================================
    "AUDIT_LOG_HEADER": """🛠 Админка › 📜 <b>Аудит-лог</b>

<i>Последние 10 действий администраторов:</i>
""",
    "AUDIT_LOG_EMPTY": "<i>Лог действий пуст.</i>",
    "AUDIT_ENTRY": """[{date}]
Admin <code>{admin_id}</code>
➡️ {action}{target}{details}
""",
    "AUDIT_ACTIONS": {
        "EXTEND": "⏰ Продлил",
        "BAN": "🚫 Забанил",
        "UNBAN": "✅ Разбанил",
        "DELETE_SERVER": "🗑 Удалил сервер",
        "ADD_SERVER": "➕ Добавил сервер",
        "TOGGLE_SERVER": "🔄 Переключил сервер",
        "EDIT_SERVER": "✏️ Изменил сервер",
        "DELETE_TARIFF": "🗑 Удалил тариф",
        "ADD_TARIFF": "➕ Добавил тариф",
        "EDIT_TARIFF": "✏️ Изменил тариф",
        "BROADCAST": "📢 Сделал рассылку",
        "CHANGE_TARIFF": "💎 Сменил тариф",
        "REDUCE": "➖ Уменьшил дни",
        "GRANT": "🎫 Выдал доступ",
        "DELETE_DEVICE": "🗑 Удалил устройство",
        "DEVICE_CREATE_BLOCKED": "🚫 Блокировка создания (daily limit)",
        "DEVICE_CREATED": "📱 Создал устройство",
        "DEVICE_DELETED": "🗑 Удалил устройство",
        "PAYMENT_SUCCESS": "✅ Платёж обработан",
        "PAYMENT_FAILED": "❌ Платёж не создан",
        "PAYMENT_CANCELLED": "❌ Платёж отменён",
        "PAYMENT_MANUAL_REVIEW": "🧪 Платёж отправлен на ручную проверку",
        "PAYMENT_CHARGEBACK": "↩️ Chargeback",
        "PAYMENT_CANCEL_AFTER_COMPLETED": "🚨 Отмена после completed",
        "PAID_AFTER_CANCEL": "⚠️ Оплата после отмены",
        "MANUAL_GRANT": "🎫 Ручная выдача",
        "STARS_PAYMENT_MANUAL_REVIEW": "🧪 Stars-платёж на проверке",
        "TOGGLE_MAINTENANCE": "🛠 Переключил техработы",
        "PLATEGA_CALLBACK": "📥 Платёжный callback",
        "TARIFF_EDIT_BLOCKED": "🚫 Блокировка изменения тарифа",
    },

    # ============================================================
    # BROADCAST
    # ============================================================
    "BROADCAST_PROMPT": """🛠 Админка › 📢 <b>Рассылка</b>

📢 Введите текст сообщения для рассылки:

Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)""",

    "BROADCAST_PREVIEW": "📢 <b>Предпросмотр рассылки ({content_type}):</b>\n\n{text}",

    "BROADCAST_RESULT": """✅ Рассылка завершена!

📤 Отправлено: {success_count}
❌ Ошибок: {fail_count}
👥 {label}: {total_count}""",

    "BROADCAST_STARTED": """🚀 <b>Рассылка запущена!</b>

Отправляю {total_count} пользователям...

Результат придёт отдельным сообщением.""",

    "BROADCAST_NO_RECIPIENTS": "⚠️ Нет получателей для рассылки",
    "BROADCAST_ALREADY_RUNNING": "⏳ Рассылка уже идёт, дождитесь завершения",
    "BROADCAST_STOPPING": "⏹ Рассылка останавливается...",

    # ============================================================
    # ADMIN USERS
    # ============================================================
    "ADMIN_USERS_HEADER": """🛠 Админка › 👥 <b>Пользователи</b>

(стр. {page}/{total_pages}) · Всего: {total}
""",
    "ADMIN_USERS_EMPTY": "<i>Пользователей пока нет</i>\n",
    "ADMIN_USER_SEARCH_PROMPT": """🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>

Введите Telegram ID пользователя:""",

    "ADMIN_USER_CARD": """🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>

<b>ID:</b> <code>{telegram_id}</code>
<b>Username:</b> @{username}
<b>Имя:</b> {first_name}
<b>Статус:</b> {status}
<b>Бан:</b> {ban}
<b>Действует до:</b> {valid_until}
<b>Осталось:</b> {days_left}
<b>Устройств:</b> {devices_count}/{device_limit}
<b>Рефералов:</b> {referrals_count}
<b>Бонусных дней:</b> +{referral_days}
<b>Регистрация:</b> {created_at}""",

    "ADMIN_USER_DEVICES_HEADER": """🛠 Админка › 👥 Пользователи › 🔧 <b>Устройства</b>

Пользователь <code>{telegram_id}</code>
""",
    "ADMIN_USER_DEVICES_EMPTY": "<i>Устройств нет</i>\n",

    "ADMIN_DELETE_DEVICE_CONFIRM": """⚠️ <b>Подтверждение удаления устройства</b>

Пользователь: <code>{telegram_id}</code>
Устройство: <b>{device_name}</b>
Сервер: {flag} {server_name}

Что произойдёт:
• Устройство будет удалено с сервера (API DELETE)
• Профиль будет удалён из БД

<i>Это действие необратимо.</i>""",

    "ADMIN_DELETE_DEVICE_SUCCESS": """✅ <b>Устройство удалено</b>

Пользователь: <code>{telegram_id}</code>
Устройство: <b>{device_name}</b>""",

    "ADMIN_DELETE_DEVICE_FAILED": "⚠️ Не удалось удалить устройство. Сервер недоступен.",
    "ADMIN_DELETE_DEVICE_ERROR": "❌ Ошибка при удалении устройства",

    # ============================================================
    # ADMIN BAN / UNBAN
    # ============================================================
    "ADMIN_BAN_CONFIRM": """⚠️ <b>Подтверждение блокировки</b>

Пользователь: <code>{telegram_id}</code>

Пользователь будет заблокирован.
Все его устройства будут удалены без возможности восстановления.
Ожидающие платежи будут отменены.

<i>После разблокировки устройства не восстанавливаются. Пользователь сможет создать их заново, если подписка активна.</i>""",

    "ADMIN_UNBAN_CONFIRM": """⚠️ <b>Подтверждение разблокировки</b>

Пользователь: <code>{telegram_id}</code>

Пользователь будет разблокирован.
Устройства не будут восстановлены.
Пользователь сможет создать их заново, если подписка активна.

<i>Это действие можно отменить повторной блокировкой.</i>""",

    "ADMIN_BAN_SUCCESS": "✅ Пользователь {message}",
    "ADMIN_BAN_FAILED": "❌ Ошибка: {message}",

    # ============================================================
    # ADMIN SUBSCRIPTION
    # ============================================================
    "ADMIN_SUBSCRIPTION_HEADER": """🛠 Админка › 👥 Пользователи › 📅 <b>Подписка</b>

Пользователь: <code>{telegram_id}</code>

━━━━━━━━━━━━━━━━━━━━

{status_block}""",

    "ADMIN_SUB_STATUS_ACTIVE": """🟢 <b>Статус:</b> Активна
💎 <b>Тариф:</b> {tariff_name}
📅 <b>Действует до:</b> {valid_until}
⏱ <b>Осталось:</b> {time_left}
🔌 <b>Устройств:</b> {devices_count} / {device_limit}""",

    "ADMIN_SUB_STATUS_INACTIVE": """🔴 <b>Статус:</b> Неактивна
💎 <b>Тариф:</b> {tariff_name}
📅 <b>Истекла:</b> {valid_until}""",

    "ADMIN_SUB_STATUS_NONE": """⚪ <b>Статус:</b> Подписка отсутствует
💎 <b>Тариф:</b> —
🔌 <b>Устройств:</b> {devices_count}""",

    "ADMIN_SUB_CHANGE_TARIFF_HEADER": """🛠 Админка › 📅 Подписка › 💎 <b>Смена тарифа</b>

Пользователь: <code>{telegram_id}</code>
Текущий тариф: <b>{current_tariff}</b>
Устройств: <b>{devices_count}</b>

━━━━━━━━━━━━━━━━━━━━

Выберите новый тариф:""",

    "ADMIN_SUB_CONFIRM_TARIFF": """⚠️ <b>Подтверждение смены тарифа</b>

Пользователь: <code>{telegram_id}</code>
Текущий: <b>{old_tariff}</b>
Новый: <b>{new_tariff}</b>
Устройств: <b>{devices_count}</b>

Что произойдёт:
• Тариф будет изменён мгновенно
• Лимит устройств обновится
• Срок подписки НЕ изменится

<i>Это действие необратимо.</i>""",

    "ADMIN_SUB_TARIFF_CHANGED": """✅ <b>Тариф успешно изменён</b>

Пользователь: <code>{telegram_id}</code>
Новый тариф: <b>{tariff_name}</b>
Лимит устройств: <b>{device_limit}</b>""",

    "ADMIN_SUB_DOWNGRADE_BLOCKED": """⚠️ <b>Смена тарифа невозможна</b>

Пользователь: <code>{telegram_id}</code>

У пользователя <b>{devices_count}</b> активных устройств,
а выбранный тариф поддерживает только <b>{new_limit}</b>.

Сначала удалите лишние устройства через
«🔧 Управление устройствами».""",

    "ADMIN_SUB_EXTEND_HEADER": """🛠 Админка › 📅 Подписка › ➕ <b>Продление</b>

Пользователь: <code>{telegram_id}</code>
Действует до: <b>{valid_until}</b>

━━━━━━━━━━━━━━━━━━━━

Выберите срок продления:""",

    "ADMIN_SUB_CONFIRM_EXTEND": """⚠️ <b>Подтверждение продления</b>

Пользователь: <code>{telegram_id}</code>
Текущая дата: <b>{current_end}</b>
Продление на: <b>{days_text}</b>
Новая дата: <b>{new_end}</b>

<i>Это действие необратимо.</i>""",

    "ADMIN_SUB_EXTEND_PROMPT": """🛠 Админка › 📅 Подписка › ⌨️ <b>Ручное продление</b>

Пользователь: <code>{telegram_id}</code>

⏱ Введите количество дней (число ≥ 1):""",

    "ADMIN_SUB_REDUCE_PROMPT": """🛠 Админка › 📅 Подписка › ➖ <b>Уменьшение дней</b>

Пользователь: <code>{telegram_id}</code>
Действует до: <b>{valid_until}</b>

⏱ Введите количество дней для уменьшения (число ≥ 1):""",

    "ADMIN_SUB_CONFIRM_REDUCE": """⚠️ <b>Подтверждение уменьшения</b>

Пользователь: <code>{telegram_id}</code>
Текущая дата: <b>{current_end}</b>
Уменьшение на: <b>{days} дн.</b>
Новая дата: <b>{new_end}</b>

⚠️ Если новая дата в прошлом — подписка истечёт мгновенно.

<i>Это действие необратимо.</i>""",

    "ADMIN_SUB_REDUCED": """✅ <b>Подписка уменьшена</b>

Пользователь: <code>{telegram_id}</code>
Новая дата: <b>{new_end}</b>""",

    "ADMIN_SUB_GRANT_HEADER": """🛠 Админка › 📅 Подписка › 🎫 <b>Выдать доступ</b>

Пользователь: <code>{telegram_id}</code>

━━━━━━━━━━━━━━━━━━━━

Выберите тариф:""",

    "ADMIN_SUB_GRANT_DAYS_HEADER": """🛠 Админка › 🎫 Выдать доступ

Пользователь: <code>{telegram_id}</code>
Тариф: <b>{tariff_name}</b>

━━━━━━━━━━━━━━━━━━━━

Выберите срок:""",

    "ADMIN_SUB_GRANT_CUSTOM_PROMPT": """🛠 Админка › 🎫 Выдать доступ › ⌨️ <b>Ручной срок</b>

Пользователь: <code>{telegram_id}</code>
Тариф: <b>{tariff_name}</b>

⏱ Введите количество дней (число ≥ 1):""",

    "ADMIN_SUB_CONFIRM_GRANT": """⚠️ <b>Подтверждение выдачи доступа</b>

Пользователь: <code>{telegram_id}</code>
Тариф: <b>{tariff_name}</b>
Срок: <b>{days_text}</b>
Действует до: <b>{new_end}</b>

<i>Это действие необратимо.</i>""",

    "ADMIN_SUB_NO_SUBSCRIPTION": "❌ У пользователя нет подписки",

    # ═══ НОВОЕ (Спринт 3): дополнительные тексты подписки ═══
    "ADMIN_SUB_GROUP_NOT_FOUND": "❌ Группа тарифов не найдена",
    "ADMIN_SUB_TARIFF_ALREADY_SELECTED": "⚠️ Этот тариф уже выбран",
    "ADMIN_SUB_PERMANENT_LABEL": "∞ навсегда",

    "ADMIN_SUB_EXTEND_SUCCESS": """✅ <b>Подписка продлена</b>

Пользователь: <code>{telegram_id}</code>
На: <b>{days_text}</b>
Действует до: <b>{new_end}</b>""",

    "ADMIN_SUB_GRANT_SUCCESS": """✅ <b>Доступ выдан</b>

Пользователь: <code>{telegram_id}</code>
Тариф: <b>{tariff_name}</b>
Срок: <b>{days_text}</b>
Действует до: <b>{new_end}</b>""",

    "ADMIN_SUB_EXTEND_FAILED": "❌ Ошибка при продлении",
    "ADMIN_SUB_REDUCE_FAILED": "❌ Ошибка при уменьшении",
    "ADMIN_SUB_GRANT_FAILED": "❌ Ошибка при выдаче доступа",
    "ADMIN_SUB_CHANGE_FAILED": "❌ Ошибка при смене тарифа",

    # ============================================================
    # ADMIN MANUAL GRANT
    # ============================================================
    "ADMIN_MANUAL_GRANT_CONFIRM": """⚠️ <b>Подтверждение ручной выдачи</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Клиент:</b> <code>{user_telegram_id}</code>
💎 <b>Тариф:</b> {tariff_name}
💰 <b>Сумма:</b> {amount} {currency}
📦 <b>Статус:</b> {status_name}

━━━━━━━━━━━━━━━━━━━━
<i>Подписка будет выдана вручную. Клиент получит уведомление.</i>""",

    "ADMIN_MANUAL_GRANT_SUCCESS_ANSWER": "✅ Подписка выдана вручную для {user_telegram_id}",

    "ADMIN_MANUAL_GRANT_SUCCESS_MESSAGE": """✅ <b>Подписка выдана вручную</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Клиент:</b> <code>{user_telegram_id}</code>
🛠 <b>Админ:</b> <code>{admin_id}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Клиент получил доступ автоматически.</i>""",

    "ADMIN_MANUAL_GRANT_FAILED": "❌ Ошибка при выдаче подписки",
    "ADMIN_MANUAL_GRANT_PAYMENT_NOT_FOUND": "❌ Платёж не найден",
    "ADMIN_MANUAL_GRANT_ALREADY_COMPLETED": "❌ Платёж уже выдан",
    "ADMIN_MANUAL_GRANT_REFUNDED": "❌ Платёж возвращён, выдача запрещена",
    "ADMIN_MANUAL_GRANT_INVALID_STATUS": "❌ Недопустимый статус платежа",
    "ADMIN_MANUAL_GRANT_USER_NOT_FOUND": "❌ Пользователь не найден",
    "ADMIN_MANUAL_GRANT_USER_DELETED": "❌ Пользователь удалён",
    "ADMIN_MANUAL_GRANT_USER_BANNED": "❌ Пользователь заблокирован. Сначала разблокируйте пользователя.",

    # ============================================================
    # ADMIN SERVERS
    # ============================================================
    "ADMIN_SERVER_CHECKING": """🔍 <b>Проверяю доступность сервера...</b>

Ожидайте, это может занять несколько секунд.""",

    "ADMIN_SERVER_ADDED": """✅ <b>Сервер добавлен и проверен!</b>

{flag} <b>{name}</b>

Протокол: {protocol}
Макс клиентов (из API): {max_clients}
API: <code>{api_url}</code>""",

    "ADMIN_SERVER_CARD": """🛠 Админка › 🌍 Серверы › {flag} <b>{name}</b>

<b>ID:</b> {id}
<b>Статус:</b> {status}
<b>Протокол:</b> {protocol}
<b>API URL:</b> {api_url}
<b>Макс клиентов:</b> {max_clients}""",

    "ADMIN_SERVER_NAME_PROMPT": """🛠 Админка › 🌍 Серверы › ➕ <b>Новый сервер</b>

✏️ Введите имя сервера (например: Нидерланды):""",

    "ADMIN_SERVER_FLAG_PROMPT": "🏳️ Введите флаг страны (эмодзи, например: 🇳🇱):",
    "ADMIN_SERVER_URL_PROMPT": "🔗 Введите API URL сервера (например: http://127.0.0.1:4001):",
    "ADMIN_SERVER_KEY_PROMPT": "🔑 Введите API ключ сервера:",

    "ADMIN_SERVER_RENAME_PROMPT": """🛠 Админка › 🌍 Серверы › ✏️ <b>Редактирование</b>

✏️ Введите новое имя сервера:""",

    "ADMIN_SERVER_RENAMED": "✅ Имя сервера изменено на: {name}",

    "ADMIN_SERVER_FLAG_PROMPT_EDIT": """🛠 Админка › 🌍 Серверы › 🏳 <b>Изменить флаг</b>

Текущий флаг: {current_flag}

Введите новый флаг страны (эмодзи, например: 🇩🇪):""",

    "ADMIN_SERVER_FLAG_UPDATED": "✅ Флаг сервера изменён на: {flag}",
    "ADMIN_SERVER_FLAG_TOO_LONG": "⚠️ Флаг слишком длинный (макс. 10 символов).",

    # ═══ НОВОЕ (Спринт 2): тексты редактирования URL/ключа/лимита ═══
    "ADMIN_SERVER_EDIT_URL_PROMPT": "🔗 Введите новый API URL сервера:",
    "ADMIN_SERVER_EDIT_KEY_PROMPT": "🔑 Введите новый API ключ сервера:",
    "ADMIN_SERVER_EDIT_MAX_CLIENTS_PROMPT": "👥 Введите новый лимит клиентов (число ≥ 1):",
    "ADMIN_SERVER_URL_UPDATED": "✅ API URL изменён на: {api_url}",
    "ADMIN_SERVER_KEY_UPDATED": "✅ API ключ обновлён и проверен",
    "ADMIN_SERVER_MAX_CLIENTS_UPDATED": "✅ Лимит клиентов изменён на: {max_clients}",
    "ADMIN_SERVER_MAX_CLIENTS_WARNING": "⚠️ На сервере {current} профилей, новый лимит {new}.",

    "ERROR_SERVER_DUPLICATE_URL": """⚠️ <b>Сервер с таким API URL уже существует!</b>

URL: <code>{api_url}</code>

Нельзя добавить один и тот же сервер дважды.""",

    "ADMIN_SERVER_DELETE_CONFIRM": """⚠️ <b>Подтверждение удаления сервера</b>

{flag} <b>{name}</b>

На этом сервере находится <b>{profiles_count}</b> активных устройств.

Что произойдёт:
• Все устройства будут удалены с сервера (API DELETE)
• Профили будут удалены из локальной БД
• Сам сервер будет удалён из системы

<i>Это действие необратимо.</i>""",

    "ADMIN_SERVER_DELETE_SUCCESS": "✅ Сервер {server_name} удалён ({deleted_profiles} устр.)",

    "ADMIN_SERVER_DELETE_BACKGROUND_PARTIAL": """⚠️ Сервер {server_name} удалён из БД ({deleted_profiles} устр.),
но {api_fail}/{total_profiles} пиров не удалось удалить из API.
Worker Cleanup подчистит позже.""",

    "ADMIN_SERVER_TOGGLE_ENABLE_CONFIRM": """⚠️ <b>Подтверждение включения сервера</b>

{flag} <b>{name}</b>

Сервер снова будет доступен пользователям
при создании новых устройств.

<i>Существующие устройства продолжат работать.</i>""",

    "ADMIN_SERVER_TOGGLE_DISABLE_CONFIRM": """⚠️ <b>Подтверждение отключения сервера</b>

{flag} <b>{name}</b>

Сервер будет скрыт из списка доступных локаций
при создании новых устройств.

<i>Существующие устройства продолжат работать.</i>""",

    "ADMIN_SERVER_TOGGLE_SUCCESS": "✅ Сервер {status}",
    "ADMIN_SERVER_STATE_ENABLED": "включен",
    "ADMIN_SERVER_STATE_DISABLED": "выключен",
    "ADMIN_SERVER_SESSION_EXPIRED": "⚠️ Сессия подтверждения истекла",

    # ============================================================
    # ADMIN TARIFFS
    # ============================================================
    "ADMIN_TARIFF_CARD": """🛠 Админка › 💰 Тарифы › <b>Тариф</b>

<b>ID:</b> {id}
<b>Дней:</b> {duration_days}
<b>Устройств:</b> {device_limit}
<b>Цена ₽:</b> {price_rub}
<b>Цена ⭐:</b> {price_stars}
<b>Статус:</b> {status}""",

    "ADMIN_TARIFF_EDIT_DAYS_PROMPT": """🛠 Админка › 💰 Тарифы › ⏱ <b>Изменить дни</b>

⏱ Введите новое количество дней:""",

    "ADMIN_TARIFF_EDIT_DEVICES_PROMPT": """🛠 Админка › 💰 Тарифы › 📱 <b>Изменить лимит устройств</b>

📱 Введите новый лимит устройств (число ≥ 1):""",

    "ADMIN_TARIFF_EDIT_RUB_PROMPT": """🛠 Админка › 💰 Тарифы › 💵 <b>Изменить цену ₽</b>

💵 Введите новую цену в рублях:""",

    "ADMIN_TARIFF_EDIT_STARS_PROMPT": """🛠 Админка › 💰 Тарифы › ⭐ <b>Изменить цену Stars</b>

⭐ Введите новую цену в Stars:""",

    "ADMIN_TARIFF_EDIT_DAYS_SUCCESS": "✅ Дни тарифа изменены на {value} дней",
    "ADMIN_TARIFF_EDIT_DEVICES_SUCCESS": "✅ Лимит устройств изменён на {value}",
    "ADMIN_TARIFF_EDIT_RUB_SUCCESS": "✅ Цена в рублях изменена на {value} ₽",
    "ADMIN_TARIFF_EDIT_STARS_SUCCESS": "✅ Цена в Stars изменена на {value} ⭐",

    "ADMIN_TARIFF_TOGGLE_ENABLE_CONFIRM": """⚠️ <b>Подтверждение включения тарифа</b>

Тариф: <b>{duration_days} дн. / {device_limit} устр.</b>

Тариф снова будет доступен пользователям
при покупке доступа.

<i>Уже купленные подписки продолжат работать.</i>""",

    "ADMIN_TARIFF_TOGGLE_DISABLE_CONFIRM": """⚠️ <b>Подтверждение отключения тарифа</b>

Тариф: <b>{duration_days} дн. / {device_limit} устр.</b>

Тариф будет скрыт из списка доступных
при покупке доступа.

<i>Уже купленные подписки продолжат работать.</i>""",

    "ADMIN_TARIFF_TOGGLE_SUCCESS_ENABLED": "✅ Тариф включен",
    "ADMIN_TARIFF_TOGGLE_SUCCESS_DISABLED": "✅ Тариф выключен",

    "ADMIN_TARIFF_DELETE_CONFIRM": """⚠️ <b>Подтверждение удаления тарифа</b>

Тариф: <b>{duration_days} дн. / {device_limit} устр.</b>
Цена: <b>{price_rub}₽ / {price_stars}⭐</b>

Тариф будет удалён безвозвратно.""",

    "ADMIN_TARIFF_DELETE_BLOCKED_PAYMENTS": """⚠️ <b>Удаление тарифа заблокировано</b>

По этому тарифу есть история платежей: <b>{payments_count}</b>.

Удаление невозможно, чтобы сохранить платёжную историю.""",

    "ADMIN_TARIFF_DELETE_BLOCKED_RELATIONS": """⚠️ <b>Удаление тарифа заблокировано</b>

Не удалось удалить тариф из-за связанных данных.

Возможно, по нему есть история платежей или активные подписки.""",

    "ADMIN_TARIFF_DELETE_SUCCESS": "✅ Тариф ({duration_days} дн., {device_limit} устр.) удалён",

    "ERROR_TARIFF_IN_USE": """⚠️ <b>Удаление тарифа заблокировано</b>

Этот тариф сейчас используют <b>{user_count}</b> активных клиентов.

Чтобы удалить тариф, сначала переведите пользователей на другой тариф или дождитесь окончания их подписок.""",

    # ============================================================
    # TARIFF EDIT GUARDS / STABILIZATION
    # ============================================================
    "ADMIN_TARIFF_EDIT_REQUIRE_MAINTENANCE": """⚠️ <b>Изменение тарифов доступно только в режиме технических работ</b>

Включите техработы, затем повторите действие.""",

    "ADMIN_TARIFF_EDIT_BLOCKED_PENDING": """⚠️ <b>Изменение тарифа заблокировано</b>

По этому тарифу есть ожидающие платежи.

Сначала обработайте или отмените их, затем измените тариф.""",

    "ADMIN_TARIFF_EDIT_BLOCKED_DEVICE_LIMIT": """⚠️ <b>Изменение лимита невозможно</b>

У пользователей больше устройств, чем новый лимит.

Сначала уменьшите количество устройств у пользователей или выберите больший лимит.""",

    "ADMIN_TARIFF_TOGGLE_BLOCKED_PENDING": """⚠️ <b>Отключение тарифа заблокировано</b>

По этому тарифу есть ожидающие платежи.

Сначала обработайте или отмените их, затем выключите тариф.""",

    # ═══ НОВОЕ (Спринт 3): имена статусов платежей ═══
    "PAYMENT_STATUS_NAMES": {
        "pending": "⏳ Ожидает",
        "completed": "✅ Завершён",
        "cancelled": "❌ Отменён",
        "failed": "⚠️ Ошибка",
        "refunded": "↩️ Возврат",
        "requires_manual_review": "🧪 Ручная проверка",
    },

    # ============================================================
    # ALERTS TO ADMINS
    # ============================================================
    "ALERT_CRITICAL_BOT_ERROR": """🚨 <b>КРИТИЧЕСКАЯ ОШИБКА БОТА</b>
━━━━━━━━━━━━━━━━━━━━

🔍 <b>Request ID:</b> <code>{request_id}</code>
⚠️ <b>Тип:</b> <code>{error_type}</code>
📝 <b>Описание:</b> <i>{error_short}</i>

━━━━━━━━━━━━━━━━━━━━
<i>Полный лог доступен через:
<code>journalctl -u projectx-bot | grep {request_id}</code></i>""",

    "ALERT_PAYMENT_MANUAL_REVIEW": """⚠️ <b>Платёж требует ручной проверки</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Клиент:</b> <code>{user_telegram_id}</code> ({username})
💎 <b>Тариф:</b> {tariff_name}
💰 <b>Сумма:</b> {amount} {currency}
🧩 <b>Причина:</b> {reason_text}
📍 <b>Источник:</b> <code>{source}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Доступ не выдан автоматически.</i>""",

    "ALERT_PAID_AFTER_CANCEL": """⚠️ <b>Оплата после отмены</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Клиент:</b> <code>{user_telegram_id}</code> ({username})
💎 <b>Тариф:</b> {tariff_name}
💰 <b>Сумма:</b> {amount} {currency}

━━━━━━━━━━━━━━━━━━━━
<i>Деньги поступили, но платёж был ранее отменён.
Клиент уведомлён автоматически.
Выберите действие:</i>""",

    "ALERT_CANCEL_AFTER_COMPLETED": """🚨 <b>Критическая платёжная ситуация</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Клиент:</b> <code>{user_telegram_id}</code> ({username})
💎 <b>Тариф:</b> {tariff_name}
💰 <b>Сумма:</b> {amount} {currency}
🔗 <b>Transaction:</b> <code>{transaction_id}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Платёж уже был completed, но пришёл CANCELED.
Требуется ручная проверка. Возможна отмена/chargeback.</i>""",

    "ALERT_CHARGEBACK": """🚨 <b>Возврат средств</b>
━━━━━━━━━━━━━━━━━━━━

💳 <b>Платёж ID:</b> <code>{payment_id}</code>
👤 <b>Пользователь:</b> <code>{user_telegram_id}</code> ({username})
💎 <b>Тариф:</b> {tariff_name}
💰 <b>Сумма:</b> {amount} {currency}
🔗 <b>Transaction:</b> <code>{transaction_id}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Доступ отозван. Устройства удалены.
Реферальные бонусы откатаны.
Клиент уведомлён автоматически.</i>""",

    "ALERT_PAYMENT_NOT_FOUND": """🚨 <b>Платёж не найден / не сопоставлен</b>
━━━━━━━━━━━━━━━━━━━━

🔗 <b>Transaction / payload:</b> <code>{transaction_id}</code>
📦 <b>Статус события:</b> <code>{status}</code>
👤 <b>Telegram ID:</b> <code>{user_telegram_id}</code>
📍 <b>Источник:</b> <code>{source}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Проверьте платёж вручную.</i>""",

    "ALERT_STALE_PAYMENTS_NEW": """⚠️ <b>Новые зависшие платежи (pending > 1ч)</b>
━━━━━━━━━━━━━━━━━━━━

Количество: <b>{count}</b>

{lines}""",

    "ALERT_STARS_MANUAL_REVIEW": """⚠️ <b>Stars-платежи требуют проверки</b>
━━━━━━━━━━━━━━━━━━━━

Платежи не подтвердились автоматически за {hours} ч.

{lines}""",

    "ALERT_AMNEZIA_SERVER_UNAVAILABLE": """⚠️ <b>Сервер Amnezia недоступен!</b>

🌍 <b>{server_name}</b>
🔗 <code>{api_url}</code>

❌ CircuitBreaker перешёл в OPEN
🔄 Попытки восстановления каждые {recovery_timeout}с

💡 Проверьте сервер вручную""",

    "ALERT_QUOTA_EXCEEDED": """⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>
━━━━━━━━━━━━━━━━━━━━

👤 <b>Пользователь:</b> <code>{telegram_id}</code>
🌍 <b>Сервер:</b> {server_name}
📊 <b>Использовано:</b> <b>{traffic_tb} TB</b>
🆔 <b>Profile ID:</b> <code>{profile_id}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Пользователь скачал более 1 TB трафика.
Рекомендуется связаться с ним или принять меры.
Доступ НЕ отключен автоматически (Fair Usage Policy).</i>""",

    "ALERT_PENDING_DELETION_EXPIRED": """🚨 <b>Не удалось удалить устройства на сервере</b>
━━━━━━━━━━━━━━━━━━━━

<b>{count}</b> записей достигли лимита попыток.

Они удалены из очереди, но могли остаться на сервере.

<i>Требуется ручная проверка.</i>""",

    "ALERT_WORKER_CRASH": """🚨 <b>Фоновый воркер упал</b>
━━━━━━━━━━━━━━━━━━━━

🧩 <b>Воркер:</b> <code>{worker_name}</code>
⚠️ <b>Ошибка:</b> <code>{error_text}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Supervisor пытается перезапустить воркер.</i>""",

    "ALERT_WORKER_CRASH_CRITICAL": """🚨 <b>Фоновый воркер не удалось восстановить</b>
━━━━━━━━━━━━━━━━━━━━

🧩 <b>Воркер:</b> <code>{worker_name}</code>
🔁 <b>Попыток перезапуска:</b> {count}

━━━━━━━━━━━━━━━━━━━━
<i>Требуется ручное вмешательство.</i>""",

    "ALERT_WORKER_SUPERVISOR_CRASH": """🚨 <b>Supervisor фоновых воркеров упал</b>
━━━━━━━━━━━━━━━━━━━━

⚠️ <b>Ошибка:</b> <code>{error_text}</code>

━━━━━━━━━━━━━━━━━━━━
<i>Требуется ручное вмешательство. Воркеры могут больше не перезапускаться.</i>""",
}