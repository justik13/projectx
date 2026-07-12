# Amnezia API
REST API for remote management of an **Amnezia**-based VPN server. It turns Amnezia's CLI management (AmneziaWG, AmneziaWG 2.0, Xray) into a convenient HTTP interface with typed schemas and Swagger out of the box.

Great for building your own infrastructure on top of Amnezia: an admin panel, a Telegram bot, billing, or balancing across multiple servers — without SSH-ing into the box.

## Features

- **Three protocols, one API** — AmneziaWG, AmneziaWG 2.0 and Xray via a single set of routes.
- **Client management** — create, list, update and delete peers; ready-to-import config string.
- **Pause users** — temporarily revoke access (`status: disabled`) without deleting the key, then resume it (`status: active`). The user doesn't have to regenerate their config.
- **Config QR codes** — generate a QR series (`POST /clients/qr`) in the same format as the Amnezia client: large configs are split into several codes that the Amnezia app scans and imports.
- **Access expiration** — `expiresAt` field per client and a background cron task that auto-disables expired clients.
- **Per-peer stats** — traffic (sent/received), last handshake, online status, endpoint and allowed IPs.
- **Server metrics** — CPU, RAM, disk, network, load average, uptime and Docker container stats for the VPN.
- **Backup & restore** — export and import the server configuration via API.
- **Balancing** — server weight, region and client limit in the `/server` response for routing across nodes.
- **Swagger UI** at `/docs` with schemas, examples and request validation.
- **Localized** responses (en/ru) and API-key authentication.

## Supported protocols

| Protocol      | `protocol` value |
| ------------- | ---------------- |
| AmneziaWG     | `amneziawg`      |
| AmneziaWG 2.0 | `amneziawg2`     |
| Xray          | `xray`           |

## Quick start

```bash
# Clone repository
git clone https://github.com/kyoresuas/amnezia-api.git

# Go to repo
cd ./amnezia-api

# Run installer (asks for pm2 or docker mode)
bash ./scripts/setup.sh
```

The script installs dependencies, generates `.env`, starts the API and configures nginx. After install the API is available at `http://<your_server>`.

### Run with Docker

```bash
docker compose up -d --build
```

`docker-compose.yml` mounts `docker.sock` so the API can manage Amnezia containers on the host. Configuration is read from `.env`.

## Configuration

The `.env` file is generated automatically from `.env.example` on the first `setup.sh` run.

| Variable             | Description                                              |
| -------------------- | -------------------------------------------------------- |
| `FASTIFY_ROUTES`     | API host and port, e.g. `localhost:4001`                 |
| `FASTIFY_API_KEY`    | API access key (the `x-api-key` header)                  |
| `PROTOCOLS_ENABLED`  | Enabled protocols: `amneziawg,amneziawg2,xray`           |
| `SERVER_ID`          | Unique server identifier                                 |
| `SERVER_NAME`        | Server name (shown in the client)                        |
| `SERVER_REGION`      | Server region/zone/label                                 |
| `SERVER_WEIGHT`      | Server weight for balancing (recommended `1..1000`)      |
| `SERVER_MAX_PEERS`   | Maximum number of clients per server                     |
| `SERVER_PUBLIC_HOST` | External host/domain for the `Endpoint`                  |

## Authentication

All routes (except `/healthz`, `/metrics`, `/docs`) are protected by a preHandler and require the header:

```
x-api-key: <FASTIFY_API_KEY>
```

## Endpoints

| Method   | Route            | Purpose                                |
| -------- | ---------------- | -------------------------------------- |
| `GET`    | `/clients`       | List clients with traffic and statuses |
| `POST`   | `/clients`       | Create a client and get its config     |
| `PATCH`  | `/clients`       | Update a client (status, expiration)   |
| `POST`   | `/clients/qr`    | Config QR codes (Amnezia client format)|
| `DELETE` | `/clients`       | Delete a client                        |
| `GET`    | `/server`        | Server info and protocols              |
| `GET`    | `/server/load`   | Load metrics (CPU/RAM/disk/network)    |
| `GET`    | `/server/backup` | Export configuration backup            |
| `POST`   | `/server/backup` | Import configuration backup            |
| `POST`   | `/server/reboot` | Reboot the server                      |
| `GET`    | `/healthz`       | Healthcheck                            |
| `GET`    | `/metrics`       | Prometheus metrics                     |

## Request examples

The base URL below is `http://<your_server>`. Put your key into `x-api-key`.

### Create a client

```bash
curl -X POST http://<your_server>/clients \
  -H "x-api-key: <FASTIFY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "clientName": "Kyoresuas",
    "protocol": "amneziawg2",
    "expiresAt": null
  }'
```

```json
{
  "message": "Client created",
  "client": {
    "id": "PF77ZXRl1yAkFzhBq/zQNlDPD73XXTq+Zs2PgtjLKVA=",
    "config": "vpn://3fa85f64-5717-4562-b3fc-2c963f66afa6...",
    "protocol": "amneziawg2"
  }
}
```

### List clients

```bash
curl "http://<your_server>/clients?skip=0&limit=100" \
  -H "x-api-key: <FASTIFY_API_KEY>"
```

### Pause / resume / set expiration

`status: disabled` revokes access without deleting the key; `status: active` restores it — the user keeps the same config.

```bash
curl -X PATCH http://<your_server>/clients \
  -H "x-api-key: <FASTIFY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "PF77ZXRl1yAkFzhBq/zQNlDPD73XXTq+Zs2PgtjLKVA=",
    "protocol": "amneziawg2",
    "status": "disabled",
    "expiresAt": 1735689600
  }'
```

### Generate config QR codes

Pass the `config` returned when the client was created. The response is an array of QR images (PNG data URIs); long configs produce several — scan them one by one in the Amnezia app.

```bash
curl -X POST http://<your_server>/clients/qr \
  -H "x-api-key: <FASTIFY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "config": "vpn://3fa85f64-5717-4562-b3fc-2c963f66afa6..."
  }'
```

```json
{
  "total": 1,
  "items": ["data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."]
}
```

### Delete a client

```bash
curl -X DELETE http://<your_server>/clients \
  -H "x-api-key: <FASTIFY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "clientId": "PF77ZXRl1yAkFzhBq/zQNlDPD73XXTq+Zs2PgtjLKVA=",
    "protocol": "amneziawg2"
  }'
```

### Server info

```bash
curl http://<your_server>/server \
  -H "x-api-key: <FASTIFY_API_KEY>"
```

## API documentation

Swagger UI is available at **`/docs`**:

- `http://<your_server>/docs`

There you can review all schemas, parameters, request examples and responses.

## Project structure

<details>
<summary>Show structure</summary>

```
├─ /scripts [scripts]
├─ /src [root]
│  ├─ /config [project initialization]
│  ├─ /constants [constants]
│  ├─ /contracts [services config]
│  ├─ /controllers [API route controllers]
│  ├─ /handlers [API request handlers]
│  ├─ /helpers [specialized helpers]
│  ├─ /locales [translations]
│  ├─ /middleware [API middleware]
│  ├─ /schemas [Swagger/validation schemas]
│  ├─ /services [services]
│  ├─ /tasks [background tasks]
│  ├─ /types [types]
│  ├─ /utils [utilities]
│  └─ main.ts [entrypoint]
├─ .env.example [config example]
└─ .env [developer config]
```

---


# Amnezia API - Полная документация (Extended Edition)

REST API для удалённого управления VPN-сервером на базе **Amnezia**. Превращает CLI-управление Amnezia (AmneziaWG, AmneziaWG 2.0, Xray) в удобный HTTP-интерфейс с типизированными схемами и Swagger из коробки.

Подходит для построения собственной инфраструктуры: админ-панель, Telegram-бот, биллинг или балансировка по нескольким серверам — без ручного захода на сервер по SSH.

## 🏗️ Архитектура и важные особенности (Под капотом)

### 1. Stateless и отсутствие Базы Данных
В API **нет встроенной базы данных** (ни PostgreSQL, ни SQLite). API выступает в роли умной прослойки (Reverse Proxy/Adapter) между вашим бэкендом и Docker-контейнерами Amnezia.
- Все данные о клиентах, ключах и статусах читаются и записываются **напрямую в конфигурационные файлы** на хосте через `docker.sock`.
- **Важно:** Если вы удалите Docker-контейнер Amnezia или очистите его `volumes`, все клиенты будут безвозвратно потеряны. Регулярно делайте бэкапы через `GET /server/backup`.

### 2. Безопасность и Reverse Proxy (Nginx)
По умолчанию API **не доступно из интернета напрямую**.
- В `docker-compose.yml` порт пробрасывается как `127.0.0.1:4001:4001`.
- Скрипт `setup.sh` автоматически устанавливает **Nginx** и настраивает его как Reverse Proxy (слушает 80 порт и проксирует на `127.0.0.1:4001`).
- **Рекомендация:** Для продакшена настройте SSL (например, через Certbot/Let's Encrypt) поверх Nginx. Открывать порт `4001` в фаерволе (`ufw`) не нужно и опасно.

### 3. Особенности работы с Xray
Для сбора статистики по протоколу Xray API использует внутренний Stats API самого Xray.
- При первой установке `setup.sh` автоматически модифицирует `server.json` внутри контейнера `amnezia-xray`, включая нужные эндпоинты.
- Статистика собирается одним эффективным запросом (`statsquery`), а не сотнями `docker exec`, что снижает нагрузку на CPU.

---

## 🚀 Основные возможности

- **Три протокола в одном API** — AmneziaWG, AmneziaWG 2.0 и Xray через единый набор маршрутов.
- **Управление клиентами** — создание, список, обновление и удаление пиров; готовый конфиг для импорта в приложение.
- **Пауза пользователей** — временное отключение доступа (`status: disabled`) без удаления ключа. Пользователю не придется заново сканировать QR-код при возобновлении (`status: active`).
- **QR-коды конфигов** — генерация серии QR (`POST /clients/qr`) в формате клиента Amnezia (большие конфиги автоматически разбиваются на несколько картинок).
- **Срок действия доступа** — поле `expiresAt` у клиента и встроенная фоновая задача (cron), автоматически отключающая истёкших клиентов.
- **Статистика по каждому пиру** — трафик (отдано/принято), последнее рукопожатие, online-статус, endpoint и разрешенные IP.
- **Метрики сервера** — CPU, RAM, диск, сеть, load average, uptime и статистика Docker-контейнеров VPN.
- **Балансировка (Multi-Node)** — вес сервера (`SERVER_WEIGHT`), регион и лимит клиентов в ответе `/server` для умной маршрутизации между нодами.
- **Swagger UI** на `/docs` со схемами, примерами и валидацией запросов.

## 📋 Поддерживаемые протоколы

| Протокол | Значение `protocol` |
|----------|---------------------|
| AmneziaWG | `amneziawg` |
| AmneziaWG 2.0 | `amneziawg2` |
| Xray | `xray` |

---

---

## ⚙️ Конфигурация (.env)

Файл `.env` генерируется автоматически из `.env.example` при первом запуске.

| Переменная | Описание |
|------------|----------|
| `FASTIFY_ROUTES` | Хост и порт API внутри контейнера (например, `0.0.0.0:4001`) |
| `FASTIFY_API_KEY` | **Секретный ключ** доступа к API (заголовок `x-api-key`) |
| `PROTOCOLS_ENABLED` | Включенные протоколы: `amneziawg,amneziawg2,xray` |
| `SERVER_ID` | Уникальный идентификатор сервера |
| `SERVER_NAME` | Название сервера (отображается в клиенте) |
| `SERVER_REGION` | Регион/зона/лейбл сервера (для логики биллинга) |
| `SERVER_WEIGHT` | Вес сервера для балансировки (рекомендуется `1..1000`) |
| `SERVER_MAX_PEERS` | Максимальное число клиентов на сервере |
| `SERVER_PUBLIC_HOST` | Внешний IP/домен для `Endpoint` (подставляется в конфиги) |
| `DOCKER_API_VERSION` | Версия Docker API (по умолчанию `1.41`) |

---

## 🔐 Аутентификация

Все маршруты (кроме `/healthz`, `/metrics`, `/docs`) защищены preHandler-ом и требуют заголовок:
```http
x-api-key: <ВАШ_FASTIFY_API_KEY>
```

---

## 📡 API Endpoints (Шпаргалка)

### Управление клиентами

| Метод | Маршрут | Назначение |
|-------|---------|------------|
| `GET` | `/clients` | Список клиентов с трафиком и статусами |
| `POST` | `/clients` | Создать клиента и получить конфиг |
| `PATCH` | `/clients` | Обновить клиента (статус, срок действия) |
| `POST` | `/clients/qr` | QR-коды конфига (формат клиента Amnezia) |
| `DELETE` | `/clients` | Удалить клиента |

### Управление сервером

| Метод | Маршрут | Назначение |
|-------|---------|------------|
| `GET` | `/server` | Информация о сервере, весах и лимитах |
| `GET` | `/server/load` | Метрики нагрузки (CPU/RAM/диск/сеть) |
| `GET` | `/server/backup` | Выгрузить бэкап конфигурации (JSON) |
| `POST` | `/server/backup` | Импортировать бэкап конфигурации |
| `POST` | `/server/reboot` | Перезагрузить сервер |

### Служебные

| Метод | Маршрут | Назначение |
|-------|---------|------------|
| `GET` | `/healthz` | Healthcheck (для Docker/CI) |
| `GET` | `/metrics` | Метрики Prometheus (открыт без ключа) |

---

## 📖 Примеры использования (cURL)

### ➕ Создание клиента
```bash
curl -X POST http://<ваш_сервер>/clients \
-H "x-api-key: <API_KEY>" \
-H "Content-Type: application/json" \
-d '{
  "clientName": "User1",
  "protocol": "amneziawg2",
  "expiresAt": null
}'
```

### ⏸️ Пауза / Возобновление / Срок действия
```bash
curl -X PATCH http://<ваш_сервер>/clients \
-H "x-api-key: <API_KEY>" \
-H "Content-Type: application/json" \
-d '{
  "clientId": "PF77ZXRl1yAkFzhBq/zQNlDPD73XXTq+Zs2PgtjLKVA=",
  "protocol": "amneziawg2",
  "status": "disabled",
  "expiresAt": 1735689600
}'
```

### 📱 Генерация QR-кодов
```bash
curl -X POST http://<ваш_сервер>/clients/qr \
-H "x-api-key: <API_KEY>" \
-H "Content-Type: application/json" \
-d '{ "config": "vpn://3fa85f64-5717-4562-b3fc-2c963f66afa6..." }'
```
*Ответ: массив PNG (Data URI). Сканируйте по очереди в приложении Amnezia.*

---

---

## 🐛 Troubleshooting (Решение проблем)

1. **API не отвечает из интернета, но работает локально:**
   - Это нормально. API слушает `127.0.0.1:4001`. Убедитесь, что Nginx запущен (`systemctl status nginx`) и проксирует трафик. Настраивайте DNS и SSL на уровне Nginx.
2. **Статистика по Xray показывает нули:**
   - Убедитесь, что `setup.sh` был запущен до конца. Он должен был пропатчить `server.json` внутри контейнера `amnezia-xray` и перезапустить его.
3. **Ошибка "Permission denied" при работе с Docker:**
   - В `docker-compose.yml` используется `group_add: "${DOCKER_GID:-999}"`. Убедитесь, что пользователь, запускающий API, имеет права на чтение `/var/run/docker.sock`.
4. **Клиенты пропали после перезагрузки сервера:**
   - Если вы используете Docker-режим, убедитесь, что контейнеры Amnezia настроены на `restart: unless-stopped`. API stateless, он читает конфиги из файлов Amnezia при каждом запросе.

---
