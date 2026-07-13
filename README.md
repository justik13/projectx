# 🚀 ProjectX — Telegram-бот для управления Amnezia VPN

Многофункциональный Telegram-бот для автоматизированного управления VPN-инфраструктурой на базе **AmneziaWG 2.0** через REST API.

> ⚠️ **Проект в стадии активного тестирования.** Релизная документация будет добавлена позже.

---

## 🎯 Назначение

Бот является прослойкой между конечными пользователями и серверами Amnezia API:
- Автоматизирует создание и удаление VPN-профилей
- Управляет подписками и тарифами
- Предоставляет пользователям готовые конфигурации для импорта в клиенты Amnezia
- Даёт администраторам инструменты управления серверами и пользователями

## 🛠 Технологический стек

| Компонент | Технология |
|---|---|
| Язык | Python 3.11+ |
| Telegram-фреймворк | aiogram 3.x |
| ORM | SQLAlchemy 2.0 (async) |
| База данных | SQLite + aiosqlite |
| HTTP-клиент | aiohttp |
| Шифрование | cryptography (Fernet) |
| Взаимодействие с VPN | REST API (`kyoresuas/amnezia-api`) |

## 📦 Основные модули

```
projectx/
├── bot/                  # Telegram-интерфейс (хендлеры, клавиатуры, middleware)
├── database/             # Модели SQLAlchemy и репозитории
├── services/             # Бизнес-логика (Amnezia-клиент, подписки, платежи)
├── utils/                # Утилиты (парсер vpn://, билдер конфигов, шифрование)
└── config/               # Настройки через pydantic-settings
```

## 📚 Документация

- **`amnezia_docs.md`** — техническая справка по протоколу AmneziaWG 2.0 и API
- **`bot/texts.py`** — актуальные тексты интерфейса
- **`config/settings.py`** — описание переменных окружения

## ⚡ Быстрый старт

```bash
# 1. Клонировать репозиторий
git clone https://github.com/justik13/projectx.git
cd projectx

# 2. Установить зависимости
pip install -r requirements.txt

# 3. Создать .env (пример в .env.example)
cp .env.example .env
# Заполнить BOT_TOKEN, ADMIN_IDS, DB_ENCRYPTION_KEY

# 4. Запустить
python -m bot.main
```

## 🔗 Зависимости проекта

- **[Amnezia API](https://github.com/kyoresuas/amnezia-api)** — REST-обёртка над Amnezia
- **[AmneziaVPN Client](https://github.com/amnezia-vpn/amnezia-client)** — основной клиент
- **[AmneziaWG Architect](https://github.com/Vadim-Khristenko/AmneziaWG-Architect)** — валидатор параметров

README описывает **архитектурный уровень** проекта, а не его реализацию. Детали живут в коде и отдельных документах (`amnezia_docs.md`), которые обновляются по мере необходимости.

[![codecov](https://codecov.io/gh/justik13/projectx/branch/main/graph/badge.svg)](https://codecov.io/gh/justik13/projectx)
[![Tests](https://github.com/justik13/projectx/actions/workflows/ci.yml/badge.svg)](https://github.com/justik13/projectx/actions/workflows/ci.yml)
