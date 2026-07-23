# ProjectX — Операционный Runbook

> Личный коммерческий Telegram VPN-бот.
> Инфраструктура: 1 VPS для бота + 4 VPS для Amnezia API.
> Стек: Python, aiogram 3, SQLAlchemy async, PostgreSQL, Redis, Amnezia API, Platega, Telegram Stars.

---

## Содержание

1. [Быстрая диагностика](#1-быстрая-диагностика)
2. [Бот не отвечает / упал](#2-бот-не-отвечает--упал)
3. [Платежи](#3-платежи)
4. [Amnezia API и серверы](#4-amnezia-api-и-серверы)
5. [Устройства](#5-устройства)
6. [Подписка и доступ](#6-подписка-и-доступ)
7. [Бэкап и восстановление](#7-бэкап-и-восстановление)
8. [Деплой и обновление](#8-деплой-и-обновление)
9. [Безопасность](#9-безопасность)
10. [Мониторинг и алерты](#10-мониторинг-и-алерты)
11. [Регламентные работы](#11-регламентные-работы)
12. [Контакты и эскалация](#12-контакты-и-эскалация)

---

## 1. Быстрая диагностика

### Чек-лист «бот не работает»

```bash
# 1. Статус сервиса
systemctl status projectx-bot

# 2. Последние 50 строк лога
journalctl -u projectx-bot -n 50 --no-pager

# 3. PostgreSQL жив?
systemctl status postgresql
sudo -u postgres psql -c "SELECT 1;"

# 4. Redis жив?
systemctl status redis-server
redis-cli -a "$(grep REDIS_PASSWORD /opt/projectx-bot/.env | cut -d"'" -f2)" ping

# 5. Heartbeat-файл (обновляется каждые 60 сек)
cat /opt/projectx-bot/.heartbeat
# Если файл старше 5 минут — бот завис или упал.

# 6. Webhook-сервер (Platega)
curl -s http://127.0.0.1:8080/health
# Ожидается: OK

# 7. Nginx (если настроен SSL)
systemctl status nginx
curl -s -o /dev/null -w "%{http_code}" https://ТВОЙ_ДОМЕН/webhook/platega
```

### Быстрые команды

| Действие | Команда |
|---|---|
| Перезапустить бота | `systemctl restart projectx-bot` |
| Остановить бота | `systemctl stop projectx-bot` |
| Логи в реальном времени | `journalctl -u projectx-bot -f` |
| Логи за последний час | `journalctl -u projectx-bot --since "1 hour ago"` |
| Поиск по request_id | `journalctl -u projectx-bot \| grep <request_id>` |
| Статус PostgreSQL | `systemctl status postgresql` |
| Статус Redis | `systemctl status redis-server` |
| Создать бэкап | `/usr/local/bin/projectx-backup.sh` |
| Восстановить из бэкапа | `/usr/local/bin/projectx-restore.sh <YYYYMMDD_HHMMSS>` |

---

## 2. Бот не отвечает / упал

### Симптом: бот не реагирует на сообщения

**Шаг 1.** Проверь статус:

```bash
systemctl status projectx-bot
```

**Шаг 2.** Если `inactive (dead)` или `failed`:

```bash
# Посмотри причину
journalctl -u projectx-bot -n 100 --no-pager

# Перезапусти
systemctl restart projectx-bot

# Проверь через 10 секунд
sleep 10 && systemctl status projectx-bot
```

**Шаг 3.** Если бот в цикле рестартов (restart loop):

```bash
# Останови
systemctl stop projectx-bot

# Посмотри полный лог
journalctl -u projectx-bot -n 500 --no-pager

# Типичные причины:
# - DB_ENCRYPTION_KEY пуст или невалиден
# - BOT_TOKEN невалиден
# - PostgreSQL не доступен
# - Redis не доступен
# - PLATEGA_MERCHANT_ID задан, но PLATEGA_SECRET пуст
```

**Шаг 4.** Если бот работает, но не отвечает:

```bash
# Проверь heartbeat
cat /opt/projectx-bot/.heartbeat

# Если timestamp старше 300 секунд — бот завис.
# Принудительный рестарт
systemctl restart projectx-bot
```

### Симптом: бот отвечает, но с ошибками

```bash
# Ищи ERROR и CRITICAL в логах
journalctl -u projectx-bot --since "30 min ago" | grep -E "ERROR|CRITICAL"

# Ищи конкретный request_id (приходит в алерте админу)
journalctl -u projectx-bot | grep <request_id>
```

### Симптом: healthcheck перезапускает бота слишком часто

```bash
# Проверь crash-count
cat /opt/projectx-bot/.crash-count

# Если >= 5, healthcheck перестаёт рестартовать.
# Сбросить:
rm /opt/projectx-bot/.crash-count
systemctl restart projectx-bot
```

---

## 3. Платежи

### 3.1. Зависший платёж (pending > 1 часа)

Бот автоматически отправляет алерт админу. Если нужно проверить вручную:

```bash
# Подключись к БД
sudo -u postgres psql -d projectx_bot

# Найди зависшие платежи
SELECT id, user_id, amount, currency, status, created_at, external_id
FROM payments
WHERE status = 'pending'
AND created_at < NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC;
```

**Действия:**

1. Для RUB-платежей (SBP): бот автоматически проверяет статус через Platega API каждые 15 минут.
2. Для Stars-платежей: через 24 часа бот переводит в `requires_manual_review`.
3. Ручная выдача: через админку бота → кнопка «✅ Выдать подписку» в алерте.

### 3.2. Платёж в статусе `requires_manual_review`

```sql
-- Посмотреть все платежи на ручной проверке
SELECT id, user_id, amount, currency, status, manual_review_reason, created_at
FROM payments
WHERE status = 'requires_manual_review'
ORDER BY created_at DESC;
```

**Действия:**

1. Проверь причину (`manual_review_reason`):
   - `amount_mismatch` — сумма не совпадает. Проверь в Platega.
   - `banned_or_deleted` — пользователь забанен. Реши: разбанить или отклонить.
   - `inactive_tariff` — тариф отключён. Включи тариф или выдай вручную.
   - `device_limit_exceeded` — у пользователя больше устройств, чем лимит тарифа.
   - `stars_not_confirmed` — Stars-платёж не подтвердился за 24 часа.
2. Выдай вручную через админку или отклони.

### 3.3. Chargeback (возврат средств)

Бот обрабатывает автоматически:
- Отзывает доступ (subscription_end = NOW).
- Удаляет все устройства пользователя.
- Откатывает реферальные бонусы.
- Отправляет алерт админу.

**Если нужно проверить:**

```sql
SELECT id, user_id, amount, currency, status, created_at, paid_at
FROM payments
WHERE status = 'refunded'
ORDER BY created_at DESC
LIMIT 10;
```

### 3.4. Оплата после отмены (paid_after_cancel)

Бот обрабатывает автоматически:
- НЕ выдаёт доступ.
- Уведомляет клиента.
- Отправляет алерт админу с кнопкой «Выдать подписку».

**Действия:** реши вручную — выдать доступ или оформить возврат.

### 3.5. Platega webhook не работает

```bash
# Проверь webhook-сервер
curl -s http://127.0.0.1:8080/health
# Ожидается: OK

# Проверь nginx (если настроен)
curl -s -o /dev/null -w "%{http_code}" https://ТВОЙ_ДОМЕН/webhook/platega
# Ожидается: 401 (без заголовков) или 400 (без тела)

# Проверь, что порт 8080 не торчит наружу
ss -tlnp | grep 8080
# Должно быть: 127.0.0.1:8080, НЕ 0.0.0.0:8080

# Проверь логи webhook
journalctl -u projectx-bot | grep "Payment webhook"
```

### 3.6. Проверка статуса платежа в Platega

```bash
# Замени TRANSACTION_ID на реальный
curl -s -H "X-MerchantId: ТВОЙ_MERCHANT_ID" \
     -H "X-Secret: ТВОЙ_SECRET" \
     "https://app.platega.io/transaction/TRANSACTION_ID" | python3 -m json.tool
```

---

## 4. Amnezia API и серверы

### 4.1. Сервер недоступен (Circuit Breaker OPEN)

Бот отправляет алерт: «⚠️ Сервер Amnezia недоступен!»

**Шаг 1.** Проверь сервер:

```bash
# С VPS бота:
curl -s -H "x-api-key: ТВОЙ_КЛЮЧ" https://СЕРВЕР:8443/healthz
curl -s -H "x-api-key: ТВОЙ_КЛЮЧ" https://СЕРВЕР:8443/server
```

**Шаг 2.** Если сервер не отвечает:

```bash
# Зайди на VPS сервера по SSH
ssh root@IP_СЕРВЕРА

# Проверь Amnezia API
pm2 status
pm2 logs amnezia-api --lines 50

# Перезапусти
pm2 restart amnezia-api

# Проверь
curl -s http://127.0.0.1:4001/healthz
```

**Шаг 3.** Circuit Breaker восстанавливается автоматически через 60 секунд.

### 4.2. Добавление нового сервера

Через админку бота:

1. 🛠 Админка → 🌍 Серверы → ➕ Добавить сервер
2. Введи имя, флаг, API URL, API ключ.
3. Бот проверит healthcheck, get_server_info, наличие протокола `amneziawg2`.

**Важно:**
- API URL должен быть HTTPS для внешних серверов.
- Для локального API (на том же VPS) нужен `ALLOW_LOCAL_HTTP=true` в `.env`.
- По умолчанию `ALLOW_LOCAL_HTTP=false` и `ALLOW_LOCAL_HTTPS=false`.
- **Порт Amnezia API: 8443** (не 443). URL всегда с портом: `https://domain:8443`.

### 4.3. Редактирование сервера

Через админку бота:

1. 🛠 Админка → 🌍 Серверы → выбери сервер.
2. Доступные действия:
   - **✏️ Изменить имя** — новое имя сервера.
   - **🏳 Изменить флаг** — новый эмодзи-флаг.
   - **🔗 Изменить URL** — новый API URL (проверяется healthcheck + get_server_info).
   - **🔑 Изменить ключ** — новый API ключ (проверяется healthcheck + get_server_info).
   - **👥 Изменить лимит** — новый max_clients (предупреждение если профилей > лимита).
   - **🔴/🟢 Включить/Выключить** — переключение is_active.
   - **🗑 Удалить сервер** — полное удаление с очисткой пиров.

**После смены URL:**
- Circuit breaker для старого URL очищается автоматически.
- Pending API deletions со старым URL могут не сработать — проверь `pending_api_deletions`.

**После смены ключа:**
- Pending API deletions со старым ключом могут не сработать.

### 4.4. Удаление сервера

Через админку бота:

1. 🛠 Админка → 🌍 Серверы → выбери сервер → 🗑 Удалить сервер.
2. Бот удалит все профили из БД и попытается удалить пиров на API.
3. Если API недоступен — пиры попадут в `pending_api_deletions`.

### 4.5. Проверка реального количества пиров на сервере

```bash
# С VPS бота:
curl -s -H "x-api-key: ТВОЙ_КЛЮЧ" \
     "https://СЕРВЕР:8443/clients?skip=0&limit=100" | python3 -c "
import sys, json
data = json.load(sys.stdin)
items = data if isinstance(data, list) else data.get('items', data.get('clients', []))
print(f'Пиров на сервере: {len(items)}')
"
```

### 4.6. Amnezia API на отдельном VPS — базовая проверка

```bash
# Зайди на VPS сервера
ssh root@IP_СЕРВЕРА

# Статус
pm2 status

# Логи
pm2 logs amnezia-api --lines 100

# Перезапуск
pm2 restart amnezia-api

# Проверка healthcheck
curl -s http://127.0.0.1:4001/healthz

# Проверка server info
curl -s -H "x-api-key: $(grep FASTIFY_API_KEY ~/amnezia-api/.env | cut -d'=' -f2)" \
     http://127.0.0.1:4001/server
```

---

## 5. Устройства

### 5.1. Устройство не создаётся

**Проверь логи:**

```bash
journalctl -u projectx-bot --since "10 min ago" | grep -i "create_device"
```

**Типичные причины:**

| Ошибка в логе | Причина | Решение |
|---|---|---|
| `NoActiveSubscription` | У пользователя нет подписки | Проверь `subscription_end` в БД |
| `Server is full` | Нет свободных слотов | Проверь `max_clients` vs количество профилей |
| `Server is disabled by admin` | Сервер отключён | Включи через админку |
| `Server is busy, try again` | Lock не освободился | Подожди 60 сек, попробуй снова |
| `Cannot verify server slots` | API недоступен в критической зоне | Проверь Amnezia API |
| `API create_user failed` | Amnezia API вернул ошибку | Проверь логи API |
| `Invalid configuration URI` | API вернул битый vpn:// | Проверь Amnezia API |
| `Device limit reached` | Лимит устройств исчерпан | Удали устройство или апгрейдни тариф |
| `Daily limit exceeded` | Суточный лимит (25/день) | Подожди до 00:00 MSK |

### 5.2. Устройство не удаляется

**Проверь:**

```bash
journalctl -u projectx-bot --since "10 min ago" | grep -i "delete_device"
```

Если API недоступен, профиль удаляется из БД, а пир попадает в `pending_api_deletions`.

### 5.3. Зомби-пиры (есть на сервере, нет в БД)

Cleanup worker автоматически чистит зомби каждые 15 минут.

**Ручная проверка:**

```sql
-- Профили в БД
SELECT peer_id, server_id, device_name FROM vpn_profiles;
```

```bash
# Пиры на сервере (см. п. 4.5)
# Сравни списки. Зомби = пиры на сервере, которых нет в БД.
```

### 5.4. Pending API deletions (неудалённые пиры)

```sql
-- Посмотреть очередь
SELECT id, server_name, peer_id, reason, attempts, last_error, created_at
FROM pending_api_deletions
ORDER BY created_at DESC;

-- Очистить записи, которые достигли лимита попыток
DELETE FROM pending_api_deletions WHERE attempts >= 10;
```

---

## 6. Подписка и доступ

### 6.1. Подписка не продлевается после оплаты

**Проверь:**

```sql
-- Статус платежа
SELECT id, user_id, status, amount, currency, paid_at, manual_review_reason
FROM payments
WHERE user_id = (SELECT id FROM users WHERE telegram_id = TELEGRAM_ID)
ORDER BY created_at DESC
LIMIT 5;

-- Подписка пользователя
SELECT telegram_id, subscription_end, device_limit, current_tariff_id, is_banned
FROM users
WHERE telegram_id = TELEGRAM_ID;
```

**Типичные причины:**
- Платёж в `requires_manual_review` → выдай вручную.
- Платёж в `pending` → проверь Platega (см. п. 3.6).
- Пользователь забанен → разбань через админку.

### 6.2. Grace-период (48 часов после истечения)

После истечения подписки:
1. Устройства отключаются на API (status=disabled).
2. Пользователь получает уведомление.
3. Через 48 часов cleanup worker удаляет устройства.

**Если нужно продлить grace:**

```sql
-- Временно сдвинь subscription_end
UPDATE users
SET subscription_end = NOW() + INTERVAL '48 hours'
WHERE telegram_id = TELEGRAM_ID;
```

### 6.3. Вечная подписка

```sql
-- Выдать вечную подписку
UPDATE users
SET subscription_end = '2100-01-01 00:00:00+00',
    notified_3d = false,
    notified_1d = false,
    notified_2h = false,
    notified_expired = false,
    notified_grace_12h = false
WHERE telegram_id = TELEGRAM_ID;
```

Или через админку: Подписка → Продлить → ∞ Навсегда.

### 6.4. Синхронизация подписки с серверами

После ручного изменения `subscription_end` в БД, нужно синхронизировать с Amnezia API.

Бот делает это автоматически через `sync_expires_to_servers` при:
- Продлении через админку
- Оплате
- Смене тарифа

Если менял напрямую в БД — перезапусти бота или подожди traffic sync (каждые 15 минут).

---

## 7. Бэкап и восстановление

### 7.1. Создание бэкапа

```bash
/usr/local/bin/projectx-backup.sh
```

Бэкапы хранятся в `/root/backups/projectx/`:
- `db_YYYYMMDD_HHMMSS.sql.gz` — дамп PostgreSQL
- `env_YYYYMMDD_HHMMSS.bak.gz` — файл .env

Автобэкап: ежедневно в 03:00 (cron).

### 7.2. Восстановление из бэкапа

```bash
# Посмотреть доступные бэкапы
/usr/local/bin/projectx-restore.sh

# Восстановить конкретный
/usr/local/bin/projectx-restore.sh 20260723_030000
```

**Что делает скрипт:**
1. Останавливает бота.
2. Терминирует все подключения к БД.
3. Восстанавливает PostgreSQL из дампа.
4. Восстанавливает `.env` (если есть в бэкапе).
5. Запускает бота.

### 7.3. Ручной дамп БД

```bash
sudo -u postgres pg_dump -Fc projectx_bot | gzip > /root/backups/projectx/manual_$(date +%Y%m%d_%H%M%S).sql.gz
```

### 7.4. Ручное восстановление

```bash
systemctl stop projectx-bot
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='projectx_bot' AND pid <> pg_backend_pid();"
zcat /root/backups/projectx/db_XXXXXXXX_XXXXXX.sql.gz | sudo -u postgres pg_restore --clean --if-exists --dbname=projectx_bot
systemctl start projectx-bot
```

---

## 8. Деплой и обновление

### 8.1. Полный деплой (с нуля)

```bash
cd /путь/к/репозиторию
sudo bash deploy.sh
```

### 8.2. Обновление кода

```bash
# 1. Синхронизируй код
cd /opt/projectx-bot
rsync -a --delete \
  --exclude='.env' \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='.git' \
  /путь/к/репозиторию/ /opt/projectx-bot/

# 2. Обнови зависимости (если менялись)
source /opt/projectx-bot/venv/bin/activate
pip install -r requirements.txt

# 3. Перезапусти
systemctl restart projectx-bot

# 4. Проверь
sleep 10 && systemctl status projectx-bot
journalctl -u projectx-bot -n 20 --no-pager
```

### 8.3. Откат

```bash
# .env бэкапы
ls -la /opt/projectx-bot/.env.backup-*

# Восстановить .env
cp /opt/projectx-bot/.env.backup-XXXXXXXXXX /opt/projectx-bot/.env
systemctl restart projectx-bot
```

### 8.4. Изменение .env

```bash
nano /opt/projectx-bot/.env

# После изменения:
chmod 600 /opt/projectx-bot/.env
chown projectx:projectx /opt/projectx-bot/.env
systemctl restart projectx-bot
```

---

## 9. Безопасность

### 9.1. Проверка логов на секреты

```bash
# Ищи потенциальные утечки
journalctl -u projectx-bot --since "24 hours ago" | grep -iE "(api.key|x-api-key|token|secret|password|Fernet|vpn://)" | head -20

# Не должно быть:
# - BOT_TOKEN
# - PLATEGA_SECRET
# - Amnezia API keys
# - vpn:// конфигов
# - приватных ключей WireGuard
```

### 9.2. Права файлов

```bash
# .env — только owner
ls -la /opt/projectx-bot/.env
# Ожидается: -rw------- 1 projectx projectx

# Redis конфиг
ls -la /etc/redis/redis.conf
# Ожидается: -rw-r----- 1 redis redis

# Проверь, что Redis только на localhost
grep "^bind" /etc/redis/redis.conf
# Ожидается: bind 127.0.0.1

# Проверь, что PostgreSQL только на localhost
grep "listen_addresses" /etc/postgresql/*/main/postgresql.conf
# Ожидается: listen_addresses = 'localhost'

# Проверь, что webhook-порт не торчит наружу
ss -tlnp | grep 8080
# Ожидается: 127.0.0.1:8080
```

### 9.3. UFW firewall

```bash
ufw status verbose

# Ожидаемые правила:
# 22/tcp ALLOW (SSH)
# 80/tcp ALLOW (HTTP)
# 443/tcp ALLOW (HTTPS)
# 8443/tcp ALLOW (Amnezia API HTTPS)
# 8080/tcp DENY (webhook internal)
# 6379/tcp DENY (Redis)
```

### 9.4. SSRF protection

В `.env`:

```
ALLOW_LOCAL_HTTP=false
ALLOW_LOCAL_HTTPS=false
```

Это запрещает боту подключаться к:
- Private IP (10.x, 172.16-31.x, 192.168.x)
- Loopback (127.0.0.1, ::1)
- Link-local (169.254.x)
- Metadata endpoints (169.254.169.254)

Для локальной разработки можно временно включить `ALLOW_LOCAL_HTTP=true`.

### 9.5. Ротация секретов

**BOT_TOKEN:**
1. Создай новый токен через @BotFather.
2. Обнови в `.env`.
3. `systemctl restart projectx-bot`.

**DB_ENCRYPTION_KEY:**
⚠️ **ВНИМАНИЕ:** Смена ключа сделает все зашифрованные данные нечитаемыми!
Не меняй ключ, если в БД есть данные.

**PLATEGA_SECRET:**
1. Сгенерируй новый в кабинете Platega.
2. Обнови в `.env`.
3. `systemctl restart projectx-bot`.

**Amnezia API keys:**
1. Сгенерируй новый ключ на VPS сервера.
2. Обнови через админку бота (🔑 Изменить ключ).

---

## 10. Мониторинг и алерты

### 10.1. Алерты, которые отправляет бот

| Алерт | Триггер | Действие |
|---|---|---|
| 🚨 КРИТИЧЕСКАЯ ОШИБКА БОТА | Необработанное исключение | Проверь логи по request_id |
| ⚠️ Сервер Amnezia недоступен | Circuit Breaker OPEN | Проверь Amnezia API (п. 4.1) |
| ⚠️ Платёж требует ручной проверки | manual_review | Проверь платёж (п. 3.2) |
| ⚠️ Оплата после отмены | paid_after_cancel | Реши: выдать или возврат (п. 3.4) |
| 🚨 Возврат средств | chargeback | Проверь, что доступ отозван (п. 3.3) |
| 🚨 Отмена после completed | CANCELED по completed | Ручная проверка |
| ⚠️ Stars-платежи требуют проверки | Stars не подтвердились за 24ч | Проверь (п. 3.2) |
| ⚠️ Новые зависшие платежи | pending > 1 часа | Проверь (п. 3.1) |
| 🚨 Не удалось удалить устройства | pending_api_deletions лимит | Ручная проверка (п. 5.4) |
| ⚠️ Fair Usage Policy | Трафик > 1 TB | Свяжись с пользователем |
| 🚨 Фоновый воркер упал | Worker crash | Проверь логи, supervisor перезапустит |

### 10.2. Heartbeat

Файл `/opt/projectx-bot/.heartbeat` обновляется каждые 60 секунд.

Healthcheck (cron каждые 5 минут) проверяет:
- Если heartbeat старше 300 секунд → рестарт бота.
- Если бот не запущен → старт.
- После 5 неудачных попыток → прекращает рестарты (защита от цикла).

### 10.3. Полезные запросы к БД

```sql
-- Активные пользователи
SELECT COUNT(*) FROM users
WHERE subscription_end > NOW() AND is_deleted = false;

-- Новые за 24 часа
SELECT COUNT(*) FROM users
WHERE created_at > NOW() - INTERVAL '24 hours' AND is_deleted = false;

-- Платежи за сегодня
SELECT status, COUNT(*), SUM(amount)
FROM payments
WHERE created_at >= CURRENT_DATE
GROUP BY status;

-- Устройства по серверам
SELECT s.name, COUNT(p.id) as profiles, s.max_clients
FROM servers s
LEFT JOIN vpn_profiles p ON p.server_id = s.id
GROUP BY s.id, s.name, s.max_clients;

-- Забаненные пользователи
SELECT telegram_id, username, first_name
FROM users
WHERE is_banned = true AND is_deleted = false;
```

---

## 11. Регламентные работы

### Ежедневно (автоматически)
- Бэкап БД и .env (03:00, cron).
- Healthcheck каждые 5 минут (cron).
- Очистка старых broadcast-записей (раз в сутки, cleanup worker).
- Очистка старых audit-логов (старше 30 дней, раз в сутки).

### Еженедельно (вручную)
- [ ] Проверить `/root/backups/projectx/` — бэкапы создаются.
- [ ] Проверить `pending_api_deletions` — нет зависших записей.
- [ ] Проверить логи на ERROR/CRITICAL.
- [ ] Проверить свободное место на диске: `df -h`.

### Ежемесячно (вручную)
- [ ] Проверить SSL-сертификат: `certbot certificates`.
- [ ] Проверить автопродление: `certbot renew --dry-run`.
- [ ] Обновить системные пакеты: `apt update && apt upgrade`.
- [ ] Проверить права файлов (п. 9.2).
- [ ] Проверить UFW: `ufw status verbose`.
- [ ] Тестовое восстановление из бэкапа на тестовой БД.

---

## 12. Контакты и эскалация

### Инфраструктура

| Компонент | Где | Доступ |
|---|---|---|
| Бот (Python) | VPS-бот, `/opt/projectx-bot` | SSH root |
| PostgreSQL | VPS-бот, localhost:5432 | `sudo -u postgres psql` |
| Redis | VPS-бот, localhost:6379 | `redis-cli -a PASSWORD` |
| Nginx | VPS-бот | `systemctl status nginx` |
| Amnezia API #1 | VPS-1, порт 8443 | SSH root, `pm2 status` |
| Amnezia API #2 | VPS-2, порт 8443 | SSH root, `pm2 status` |
| Amnezia API #3 | VPS-3, порт 8443 | SSH root, `pm2 status` |
| Amnezia API #4 | VPS-4, порт 8443 | SSH root, `pm2 status` |
| Platega | app.platega.io | Личный кабинет |
| Telegram Bot | @BotFather | Токен в .env |

### Порядок эскалации

1. **Бот не отвечает** → перезапусти (п. 2).
2. **Платёж завис** → проверь Platega, выдай вручную (п. 3).
3. **Сервер Amnezia недоступен** → зайди на VPS, перезапусти API (п. 4).
4. **БД повреждена** → восстанови из бэкапа (п. 7).
5. **Ничего не помогает** → проверь логи, ищи по request_id.

---

## Инцидент: API недоступен → дашборд

### Симптомы
- Дашборд в админке не загружается или зависает.
- В логах: `Failed to get real peer count for server`.
- Circuit Breaker в состоянии OPEN.

### Причина
`get_total_free_ips` ранее ходил в Amnezia API для каждого сервера. При мёртвом API запрос зависал на таймаут (15 сек × 3 попытки × N серверов).

### Решение (Спринт 1.3)
`get_total_free_ips` теперь использует **только кэш** (`slots_cache`, TTL 300 сек). Если кэш пуст — fallback на количество профилей из БД. API-запросы не выполняются.

### Результат
Дашборд рендерится за <1 секунды при любом состоянии API.

### Проверка
```bash
# Убедись, что в логах нет запросов к API при открытии дашборда
journalctl -u projectx-bot --since "5 min ago" | grep "get_real_peer_count"
# Должно быть пусто (если кэш заполнен)
```

---

*Последнее обновление: 2026-07-23*