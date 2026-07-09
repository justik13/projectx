# ProjectX Bot 🚀

## Production-ready Telegram бот для управления Amnezia VPN

**ProjectX Bot** — это современный, отказоустойчивый Telegram-бот для продажи и управления VPN-подключениями через Amnezia API. Поддерживает мульти-VPS, нативную оплату Stars, реферальную систему и автоматический отзыв истекших подписок.

---

## ✨ Возможности

- 🎯 **Онбординг с офертой:** Блокировка интерфейса до принятия пользовательского соглашения (ToS).
- 🌍 **Мульти-VPS:** Масштабирование на неограниченное количество серверов Amnezia.
- 💳 **Оплата:** Нативные инвойсы Telegram Stars и заглушка для Platega/СБП.
- 🎁 **Реферальная система:** Автоматическое начисление бонусных дней рефереру при первой оплате приглашенного.
- 📱 **Гибкие лимиты:** Индивидуальная настройка количества устройств для каждого пользователя.
- 🔐 **Безопасность:** Fernet-шифрование конфигов (`raw_config`) в базе данных.
- 🤖 **Фоновый воркер:** Автоматическое отключение пиров на серверах Amnezia при истечении подписки (каждые 30 минут).
- 🛡️ **Защита от Dangling Peers:** Компенсирующие транзакции при сбоях БД или API (синхронизация состояний).
- 📊 **Админ-панель:** Управление пользователями, серверами, тарифами, рассылками и статистикой.
- ⚡ **Оптимизация:** Единый `UserContextMiddleware` (всего 1 запрос к БД на событие пользователя).
- 🚀 **Production Deploy:** Скрипт `deploy.sh` (systemd, cron-бэкапы, healthcheck, logrotate).

---

## 🏗 Архитектура и Стек

- **Python 3.11+** (async/await)
- **Aiogram 3.x** — Telegram Bot Framework
- **SQLAlchemy 2.0 + aiosqlite** — Async ORM
- **aiohttp** — HTTP-клиент для Amnezia Admin API
- **pydantic-settings** — Валидация конфигурации
- **cryptography** — Fernet шифрование чувствительных данных
- **Паттерны:** Repository + Service Layer + Middleware DI

---

## 📁 Структура проекта

```text
projectx/
├── bot/
│   ├── handlers/         # Обработчики команд и callback'ов
│   │   ├── admin/        # Админ-панель (dashboard, users, servers, tariffs, broadcast)
│   │   ├── start.py      # /start и онбординг
│   │   ├── profile.py    # Профиль и рефералы
│   │   ├── connection.py # Управление VPN-устройствами
│   │   ├── payment.py    # Оплата (Stars, Platega)
│   │   └── support.py    # Поддержка и FAQ
│   ├── middlewares.py    # UserContextMiddleware (бан, ToS, контекст юзера)
│   ├── keyboards.py      # Все клавиатуры (Reply/Inline)
│   ├── states.py         # FSM состояния
│   ├── texts.py          # Текстовые шаблоны
│   └── main.py           # Точка входа и запуск воркеров
├── services/
│   ├── amnezia_client.py # HTTP-клиент Amnezia API (с retry)
│   ├── subscription.py   # Бизнес-логика подписок и рефералов
│   └── background_worker.py # Фоновые задачи
├── database/
│   ├── models.py         # SQLAlchemy модели
│   ├── connection.py     # Async engine setup
│   └── repositories/     # Data Access Layer (CRUD)
├── config/settings.py    # Pydantic настройки
├── utils/                # Форматтеры, шифрование
├── deploy.sh             # Production deployment script
└── requirements.txt
```

---

## 🚀 Быстрый старт

### Локальный запуск:

```bash
git clone https://github.com/justik13/projectx.git
cd projectx
python -m venv venv
source venv/bin/activate  # или venv\Scripts\activate на Windows
pip install -r requirements.txt
cp .env.example .env  # Заполните .env своими данными
python -m bot.main
```

### Production Deploy (VPS Ubuntu/Debian):

```bash
git clone https://github.com/justik13/projectx.git /opt/projectx-bot
cd /opt/projectx-bot
sudo bash deploy.sh
```

**Скрипт `deploy.sh` автоматически:**
- Устанавливает Python и системные зависимости.
- Создаёт виртуальное окружение и ставит пакеты.
- Инициализирует SQLite базу данных.
- Настраивает **systemd** сервис (автозапуск).
- Добавляет **cron** для ежедневных бэкапов БД (в 3:00).
- Настраивает **healthcheck** каждые 5 минут (автоперезапуск при падении + уведомление админу).
- Конфигурирует **logrotate** для логов.

---

## ⚙️ Конфигурация (.env)

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Токен от @BotFather |
| `ADMIN_IDS` | ✅ | — | Telegram ID админов (через запятую) |
| `DB_PATH` | ❌ | `./bot_data.db` | Путь к SQLite базе |
| `DB_ENCRYPTION_KEY` | ❌ | `""` | Fernet ключ (32-byte base64). *Сгенерировать: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`* |
| `DEFAULT_DEVICE_LIMIT` | ❌ | `3` | Лимит устройств по умолчанию |
| `REFERRAL_BONUS_DAYS` | ❌ | `3` | Бонус дней рефереру за первую оплату |
| `SUPPORT_USERNAME` | ❌ | `@support` | Username поддержки |

---

## 🔌 Amnezia Admin API Endpoints

Бот взаимодействует с серверами Amnezia через следующие эндпоинты:

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/clients` | Список всех клиентов и пиров |
| `POST` | `/clients` | Создание нового клиента |
| `PATCH` | `/clients` | Обновление статуса клиента (disable/enable) |
| `DELETE` | `/clients` | Удаление клиента |
| `GET` | `/server` | Информация о сервере |
| `GET` | `/server/load` | Нагрузка (CPU, RAM, диск) |
| `GET` | `/healthz` | Healthcheck |

---

## 🔐 Безопасность

- **Шифрование БД:** Поля `raw_config` и `peer_id` шифруются алгоритмом Fernet перед записью в SQLite.
- **Валидация ключей:** Бот не запустится, если `DB_ENCRYPTION_KEY` невалиден (защита от случайной записи в открытом виде).
- **HTML-экранирование:** Все пользовательские имена экранируются через `html.escape()` для предотвращения инъекций.
- **Антиспам:** Throttling middleware ограничивает частоту нажатий кнопок.
- **Синхронизация:** Если Amnezia API недоступен при удалении устройства, бот прерывает операцию и не удаляет профиль из БД (защита от "висячих" пиров).

---

## 📝 Команды бота

Согласно спецификации, бот имеет **только одну команду**: `/start` (с опциональным параметром `ref_<user_id>` для рефералов).

Все остальные взаимодействия происходят через Reply-клавиатуру (нижнее меню) и Inline-кнопки.
