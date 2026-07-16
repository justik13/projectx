# 📚 AMNEZIA WG 2.0 — ПОЛНАЯ ТЕХНИЧЕСКАЯ СПРАВКА

> **ОБЛАСТЬ ДЕЙСТВИЯ:** Документ описывает **ТОЛЬКО протокол AmneziaWG 2.0** (`amneziawg2`).
> Все остальные протоколы **намеренно исключены** и не должны использоваться.
> Используется для Telegram-бота, работающего с `kyoresuas/amnezia-api`.

---

## 🚫 ЧТО МЫ НЕ ПОДДЕРЖИВАЕМ (ИГНОРИРУЕМ)

| Протокол / Клиент | Почему не работает с нами |
|---|---|
| **Чистый WireGuard** | Не понимает AWG-обфускацию (Jc, S1-S4, H1-H4, h1-h5) |
| **AmneziaWG 1.0** (`amneziawg`) | Устарел, нет S3/S4, нет диапазонов H1-H4, нет I1-I5 |
| **AmneziaWG 1.5** | Устарел, I1-I5 только на клиенте, нет серверной синхронизации |
| **Xray** (`xray`) | Другой стек, другой API-формат |
| **OpenVPN, IKEv2, Shadowsocks, Cloak** | Не поддерживаются API |
| **J1, J2, J3** | Параметры AWG 1.0 — в AWG 2.0 их НЕТ |
| **H1-H4 как одиночные числа** | В AWG 2.0 это ОБЯЗАТЕЛЬНО диапазоны `"min-max"` |

**Единственное значение поля `protocol` в API-запросах:** `"amneziawg2"`.

---

## 📦 1. ТРИ ФОРМАТА КОНФИГУРАЦИИ

| Формат | Расширение | Для какого приложения | Содержимое |
|---|---|---|---|
| **AmneziaVPN native** | `.vpn` | **AmneziaVPN** (универсальный клиент) | Полный JSON с `containers`, `awg`, `last_config` |
| **AmneziaWG native** | `.conf` | **AmneziaWG** (отдельное приложение) | WireGuard INI + AWG 2.0 параметры |
| **vpn:// URI** | — (строка) | Оба приложения (импорт через QR/буфер) | `base64url(4-byte BE length + zlib(JSON))` |

### ⚠️ Критическое правило соответствия

- `.conf` файл **ДОЛЖЕН** содержать WireGuard INI (не JSON)
- `.vpn` файл **ДОЛЖЕН** содержать полный JSON (не INI)
- Если перепутать — **ни одно приложение не откроет файл**

---

## 📱 2. КЛИЕНТЫ И ИХ ФОРМАТЫ

### AmneziaVPN (универсальный клиент)

| Платформа | Репозиторий | Импорт |
|---|---|---|
| Windows / macOS / Linux | `amnezia-vpn/amnezia-client` | `.vpn` (JSON) или `vpn://` URI |
| Android | `amnezia-vpn/amnezia-client` | `.vpn` или `vpn://` |
| iOS | `amnezia-vpn/amnezia-client` | `.vpn` или `vpn://` |

**Это основной клиент.** Поддерживает OpenVPN, WG, AWG 1.0/1.5/2.0, Xray.

### AmneziaWG (отдельное легковесное приложение)

| Платформа | Репозиторий | Импорт |
|---|---|---|
| Windows | `amnezia-vpn/amneziawg-windows-client` | `.conf` (AWG INI) |
| macOS / iOS | `amnezia-vpn/amneziawg-apple` | `.conf` (AWG INI) |
| Android | `amnezia-vpn/amneziawg-android` | `.conf` (AWG INI) |

**Это отдельный клиент.** Поддерживает **только AmneziaWG 2.0** (и старые AWG). Быстрее, легче, меньше памяти.

### Рекомендация для бота

Отдавать пользователю **оба файла одним пакетом**:
- `device.vpn` — для AmneziaVPN (полный JSON)
- `device.conf` — для AmneziaWG (WireGuard INI + AWG 2.0)

Плюс краткая инструкция, какой файл для какого приложения.

---

## 📄 3. ФОРМАТ `.vpn` (AmneziaVPN) — Полный JSON

Это то, что возвращает `kyoresuas/amnezia-api` в поле `client.config` как `vpn://...`.

### Эталонный пример

```json
{
  "containers": [
    {
      "container": "amnesia-awg2",
      "awg": {
        "protocol_version": "2",
        "port": "1234",
        "transport_proto": "udp",
        "Jc": "4",
        "Jmin": "10",
        "Jmax": "50",
        "S1": "79",
        "S2": "115",
        "S3": "5",
        "S4": "1",
        "H1": "169154911-1234371153",
        "H2": "2057051984-2121122945",
        "H3": "2132872968-2133668229",
        "H4": "2136455412-2141801388",
        "I1": "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>",
        "I2": "",
        "I3": "",
        "I4": "",
        "I5": "",
        "last_config": "{...JSON-СТРОКА (см. ниже)...}"
      }
    }
  ],
  "defaultContainer": "amnesia-awg2",
  "description": "Germany",
  "dns1": "1.1.1.1",
  "dns2": "1.0.0.1",
  "hostName": "just1kbot.1337.cx"
}
```

### Ключевой момент: `last_config`

Поле `awg.last_config` — это **JSON-СТРОКА** (не объект!), внутри которой есть готовый WireGuard INI-файл.

Декодированный `last_config` имеет такую структуру:

```json
{
  "H1": "169154911-1234371153",
  "H2": "2057051984-2121122945",
  "H3": "2132872968-2133668229",
  "H4": "2136455412-2141801388",
  "I1": "<r 2><b 0x8580...>",
  "I2": "", "I3": "", "I4": "", "I5": "",
  "Jc": "4", "Jmin": "10", "Jmax": "50",
  "S1": "79", "S2": "115", "S3": "5", "S4": "1",
  "allowed_ips": ["0.0.0.0/0", "::/0"],
  "clientId": "dwvGfuluZKlNwickCgPb6DLiUE36icqZPiQWX/BHwBk=",
  "client_ip": "10.8.1.34",
  "client_priv_key": "uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=",
  "client_pub_key": "dwvGfuluZKlNwickCgPb6DLiUE36icqZPiQWX/BHwBk=",
  "config": "[Interface]\nAddress = 10.8.1.34/32\nDNS = 1.1.1.1, 1.0.0.1\n...",
  "hostName": "just1kbot.1337.cx",
  "mtu": "1376",
  "persistent_keep_alive": "25",
  "port": 1234,
  "psk_key": "PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=",
  "server_pub_key": "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
}
```

**Поле `config`** внутри `last_config` — это **уже готовый WireGuard INI-файл как строка**. Его можно взять как есть для `.conf`.

---

## 📄 4. ФОРМАТ `.conf` (AmneziaWG) — WireGuard INI + AWG 2.0

### Эталонный пример (ТОЧНЫЙ формат, который импортирует AmneziaWG)

```ini
[Interface]
Address = 10.8.1.34/32
DNS = 1.1.1.1, 1.0.0.1
PrivateKey = uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

h1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>
h2 = 
h3 = 
h4 = 
h5 = 

[Peer]
PublicKey = bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o=
PresharedKey = PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = just1kbot.1337.cx:1234
PersistentKeepalive = 25
```

### 🔑 КЛЮЧЕВЫЕ ОТЛИЧИЯ AWG 2.0 ОТ ОБЫЧНОГО WIREGUARD

| Параметр | AWG 2.0 | Как записывается в `.conf` |
|---|---|---|
| **Jc / Jmin / Jmax** | Обязательны (вместо J1/J2/J3) | `Jc = 4`, `Jmin = 10`, `Jmax = 50` |
| **S1, S2, S3, S4** | Все четыре обязательны | `S1 = 79`, `S2 = 115`, `S3 = 5`, `S4 = 1` |
| **H1-H4** | **ДИАПАЗОНЫ через дефис** (строки!) | `H1 = 169154911-1234371153` |
| **I1-I5 (CPS)** | В JSON: `I1`-`I5` (uppercase) | В `.conf`: **`h1`-`h5` (lowercase!)** |
| **MTU** | Рекомендовано 1376 | Обычно берётся из `last_config.mtu` |

### ⚠️ Регистр критичен!

- В JSON (`awg` секции): `I1`, `I2`, `I3`, `I4`, `I5` — **UPPERCASE**
- В `.conf` файле: `h1`, `h2`, `h3`, `h4`, `h5` — **lowercase**
- Это **пакеты инициализации (CPS)** — разные регистры для разных форматов

### ⚠️ H1-H4 — это СТРОКИ, не числа

В AWG 2.0 это **обязательно диапазоны** вида `"min-max"`. Нельзя конвертировать в `int`.

```
✅ H1 = 169154911-1234371153
❌ H1 = 169154911   (это AWG 1.0, не работает в 2.0)
```

---

## 🔐 5. ФОРМАТ `vpn://` URI

### Кодирование

```
vpn:// + base64url( 4-byte big-endian original_length + zlib_compressed_JSON )
```

### Декодирование (Python)

```python
import base64, zlib, struct, json

def decode_vpn_uri(uri: str) -> dict:
    payload = uri[6:]  # убрать vpn://
    # base64url → standard base64
    b64 = payload.replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    data = base64.b64decode(b64)
    # Первые 4 байта — длина оригинального JSON (big-endian)
    original_len = struct.unpack(">I", data[:4])[0]
    # Остальное — zlib-сжатый JSON
    json_bytes = zlib.decompress(data[4:])
    assert len(json_bytes) == original_len
    return json.loads(json_bytes.decode("utf-8"))
```

### Обратное кодирование

```python
def encode_vpn_uri(data: dict) -> str:
    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    header = struct.pack(">I", len(json_bytes))
    compressed = zlib.compress(json_bytes)
    payload = base64.urlsafe_b64encode(header + compressed).decode("ascii").rstrip("=")
    return "vpn://" + payload
```

---

## 🎛️ 6. ПАРАМЕТРЫ AWG 2.0 (полный список)

### WireGuard-базовые (стандартные)

| Параметр | Где находится | Описание |
|---|---|---|
| `Address` | `[Interface]` | IP клиента (например `10.8.1.34/32`) |
| `DNS` | `[Interface]` | DNS серверы (обычно `1.1.1.1, 1.0.0.1`) |
| `PrivateKey` | `[Interface]` | Приватный ключ клиента (base64) |
| `PublicKey` | `[Peer]` | Публичный ключ сервера (base64) |
| `PresharedKey` | `[Peer]` | PSK (опционально, base64) |
| `AllowedIPs` | `[Peer]` | Обычно `0.0.0.0/0, ::/0` |
| `Endpoint` | `[Peer]` | `host:port` сервера |
| `PersistentKeepalive` | `[Peer]` | Обычно `25` секунд |

### AWG 2.0 обфускационные (J/S/H/I)

| Параметр | Тип | Описание |
|---|---|---|
| `Jc` | int | Количество junk-пакетов перед handshake |
| `Jmin` | int | Минимальный размер junk-пакета (байты) |
| `Jmax` | int | Максимальный размер junk-пакета (байты) |
| `S1` | int | Размер Initial packet |
| `S2` | int | Размер Response packet |
| `S3` | int | Размер Cookie-response (≤ 64) |
| `S4` | int | Размер Data-prefix (≤ 32) |
| `H1` | string `"min-max"` | Диапазон заголовка 1 |
| `H2` | string `"min-max"` | Диапазон заголовка 2 |
| `H3` | string `"min-max"` | Диапазон заголовка 3 |
| `H4` | string `"min-max"` | Диапазон заголовка 4 |
| `I1` / `h1` | string | CPS пакет инициализации 1 |
| `I2` / `h2` | string | CPS пакет 2 |
| `I3` / `h3` | string | CPS пакет 3 |
| `I4` / `h4` | string | CPS пакет 4 |
| `I5` / `h5` | string | CPS пакет 5 |

### Метаданные JSON

| Поле | Где | Описание |
|---|---|---|
| `containers` | верхний уровень | Массив контейнеров протоколов |
| `defaultContainer` | верхний уровень | `"amnesia-awg2"` |
| `description` | верхний уровень | Название сервера (отображается в клиенте) |
| `dns1`, `dns2` | верхний уровень | DNS серверы |
| `hostName` | верхний уровень | Hostname/IP сервера |
| `protocol_version` | внутри `awg` | `"2"` для AWG 2.0 |
| `port` | внутри `awg` | UDP порт сервера |
| `transport_proto` | внутри `awg` | `"udp"` |
| `last_config` | внутри `awg` | **JSON-строка** с готовым конфигом |
| `client_ip` | внутри `last_config` | IP клиента |
| `client_priv_key` | внутри `last_config` | Приватный ключ |
| `client_pub_key` | внутри `last_config` | Публичный ключ клиента |
| `server_pub_key` | внутри `last_config` | Публичный ключ сервера |
| `psk_key` | внутри `last_config` | Preshared key |
| `mtu` | внутри `last_config` | MTU (обычно `"1376"`) |
| `persistent_keep_alive` | внутри `last_config` | Keepalive в секундах |
| `allowed_ips` | внутри `last_config` | Массив `["0.0.0.0/0", "::/0"]` |

---

## 🚧 7. ОГРАНИЧЕНИЯ ПАРАМЕТРОВ (из AmneziaWG-Architect)

**Нарушение = конфиг не работает!**

| Правило | Ограничение | Причина |
|---|---|---|
| `S4` | `≤ 32` | Data prefix не более 32 байт |
| `S3` | `≤ 64` | Cookie-ответ не более 64 байт |
| `S1 + 56` | `≠ S2` | Init и Response не совпадают по длине |
| `S2 + 92` | `≠ S3` | Response и Cookie не совпадают |
| `H1, H2, H3, H4` | **Диапазоны не пересекаются** | Каждый заголовок уникален |
| `Jc` | `≥ 4` | Минимум 4 junk-пакета |
| `Jmax` | `> 81` | Минимальный размер junk |

**Важно:** Эти ограничения **соблюдает сам Amnezia API** при создании клиента. Боту нужно просто отдавать то, что пришло из API, **не изменяя параметры**.

---

## 🌐 8. AMNEZIA API (`kyoresuas/amnezia-api`) — ENDPOINTS

### Аутентификация

Все endpoints (кроме `/healthz`, `/metrics`, `/docs`) требуют заголовок:
```
x-api-key: <FASTIFY_API_KEY>
```

### Используемые endpoints (только `amneziawg2`)

| Метод | Маршрут | Назначение |
|---|---|---|
| `POST` | `/clients` | Создать клиента → получить `vpn://` URI |
| `GET` | `/clients?skip=0&limit=100` | Список клиентов с трафиком и статусами |
| `PATCH` | `/clients` | Обновить статус (`disabled`/`active`) и `expiresAt` |
| `DELETE` | `/clients` | Удалить клиента |
| `POST` | `/clients/qr` | Сгенерировать серию QR-кодов |
| `GET` | `/server` | Информация о сервере |
| `GET` | `/server/load` | Метрики нагрузки (CPU/RAM/диск/сеть) |
| `GET` | `/healthz` | Healthcheck |

### Создание клиента (AWG 2.0)

**Запрос:**
```bash
curl -X POST http://<server>/clients \
-H "x-api-key: <KEY>" \
-H "Content-Type: application/json" \
-d '{
  "clientName": "tg_123456_iPhone_a1b2",
  "protocol": "amneziawg2",
  "expiresAt": null
}'
```

**Ответ:**
```json
{
  "message": "Client created",
  "client": {
    "id": "PF77ZXRl1yAkFzhBq/zQNlDPD73XXTq+Zs2PgtjLKVA=",
    "config": "vpn://AAAJBXjatVZbT-M4...",
    "protocol": "amneziawg2"
  }
}
```

- `id` → сохраняется в БД как `peer_id` (используется для PATCH/DELETE)
- `config` → сохраняется в БД как `raw_config` (это `vpn://` URI)

### Пауза доступа (без удаления ключа)

`status: disabled` **отключает доступ, не удаляя ключ**. Конфиг у пользователя остаётся прежним.

```bash
curl -X PATCH http://<server>/clients \
-H "x-api-key: <KEY>" \
-d '{
  "clientId": "PF77ZXRl1y...",
  "protocol": "amneziawg2",
  "status": "disabled"
}'
```

Для возобновления: `"status": "active"`.

### Срок действия

`expiresAt` — **Unix timestamp в секундах** (UTC) или `null` для бессрочного доступа.
Фоновая задача API автоматически отключает истёкших клиентов по cron.

### QR-коды

```bash
curl -X POST http://<server>/clients/qr \
-d '{"config": "vpn://..."}'
```

**Ответ:**
```json
{
  "total": 1,
  "items": ["data:image/png;base64,iVBORw0KGgo..."]
}
```

Большие конфиги разбиваются на несколько QR — Amnezia-клиент сканирует их по очереди.

---

## 🤖 9. ЛОГИКА ГЕНЕРАЦИИ ФАЙЛОВ В БОТЕ

### Файл 1: `device.vpn` (для AmneziaVPN)

```python
def build_vpn_file(vpn_uri: str) -> str:
    data = decode_vpn_uri(vpn_uri)  # весь JSON как dict
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
```

Отдавать как `{device_name}.vpn` с MIME `application/json` или `application/octet-stream`.

### Файл 2: `device.conf` (для AmneziaWG)

**ПРИОРИТЕТ №1:** Взять готовый INI из `last_config.config`:

```python
def build_conf_file(vpn_uri: str) -> str:
    data = decode_vpn_uri(vpn_uri)
    last_config_str = data["containers"][0]["awg"]["last_config"]
    last_config = json.loads(last_config_str)
    return last_config["config"]  # Уже готовый INI!
```

**ПРИОРИТЕТ №2 (fallback):** Собрать вручную, соблюдая формат:
1. `[Interface]`: Address, DNS, MTU (если есть), PrivateKey
2. `Jc`, `Jmin`, `Jmax`
3. `S1`, `S2`, `S3`, `S4`
4. `H1`, `H2`, `H3`, `H4` **как строки** (без приведения к int!)
5. Пустая строка
6. **`h1`, `h2`, `h3`, `h4`, `h5` (lowercase!)** — из `I1`-`I5` в JSON
7. Пустая строка
8. `[Peer]`: PublicKey, PresharedKey, AllowedIPs, Endpoint, PersistentKeepalive

### Отправка в Telegram

```python
# 1. Отправить .vpn
await send_hub_document(bot, chat_id, vpn_file, caption="...", ...)
# 2. Сразу отправить .conf (отдельным сообщением)
await bot.send_document(chat_id, conf_file, caption="...")
# 3. Отправить текстовый хаб с инструкцией
await render_hub(bot, chat_id, INSTRUCTION_TEXT, back_keyboard)
```

---

## ⚠️ 10. ЖЁСТКИЕ ПРАВИЛА ДЛЯ БОТА

1. **Никогда не отдавать `.conf` с JSON-содержимым** — AmneziaWG не откроет
2. **Никогда не отдавать `.vpn` с INI-содержимым** — AmneziaVPN не откроет
3. **H1-H4 в AWG 2.0 — это строки-диапазоны** (`"min-max"`), не `int`
4. **В `.conf` файле CPS = `h1`-`h5` (lowercase!)**, хотя в JSON это `I1`-`I5`
5. **Не трогать MTU** — использовать значение из `last_config.mtu` (обычно 1376)
6. **Всегда сохранять `vpn://` URI в БД** (поле `raw_config`) — это единственный источник истины
7. **При пересоздании устройства** — новый `vpn://`, новые ключи, новый `last_config`
8. **Не менять параметры AWG 2.0** — они валидны от API, изменения сломают конфиг
9. **Для `protocol` всегда использовать `"amneziawg2"`** — никаких `"amneziawg"` (это AWG 1.0)
10. **Имя клиента в API** — формат `tg_{telegram_id}_{device_name}_{4-char-hash}` для трассируемости

---

## 🔗 11. ССЫЛКИ

### Официальные репозитории Amnezia

- **AmneziaVPN (основной клиент):** https://github.com/amnezia-vpn/amnezia-client
- **AmneziaWG Windows:** https://github.com/amnezia-vpn/amneziawg-windows-client
- **AmneziaWG Apple (macOS/iOS):** https://github.com/amnezia-vpn/amneziawg-apple
- **AmneziaWG Android:** https://github.com/amnezia-vpn/amneziawg-android
- **Все репозитории:** https://github.com/orgs/amnezia-vpn/repositories

### API

- **Amnezia API (наш API):** https://github.com/kyoresuas/amnezia-api
- **English README:** https://raw.githubusercontent.com/kyoresuas/amnezia-api/refs/heads/main/README_EN.md
- **Документация API:** `http://<server>/docs` (Swagger UI)

### Генераторы и валидаторы

- **AmneziaWG Architect (онлайн):** https://architect.vai-rice.space/
- **AmneziaWG Architect (исходники):** https://github.com/Vadim-Khristenko/AmneziaWG-Architect
- **Пул доменов CPS:** ~540 хостов (QUIC Initial, TLS ClientHello, DTLS, SIP)

### Документация

- **Amnezia Docs:** https://docs.amnezia.org
- **Amnezia Website:** https://amnezia.org

---

## 📋 12. БЫСТРАЯ ПРОВЕРКА (чеклист)

При генерации конфигов для нового устройства:

- [ ] API-запрос: `protocol = "amneziawg2"` (не `"amneziawg"`)
- [ ] Получен `vpn://...` URI от API
- [ ] URI декодирован через `base64url + zlib` (4-byte header)
- [ ] В JSON есть `containers[0].awg.last_config` (строка)
- [ ] `last_config` распарсен как JSON
- [ ] Для `.vpn`: `json.dumps(весь_json, indent=2, ensure_ascii=False)`
- [ ] Для `.conf`: взять `last_config["config"]` как есть
- [ ] В `.conf` параметры `h1-h5` записаны **lowercase** (не `I1-I5`)
- [ ] В `.conf` `H1-H4` записаны как **строки** (`"169154911-1234371153"`)
- [ ] Оба файла отправлены пользователю + инструкция
- [ ] `vpn://` URI сохранён в БД в поле `raw_config`
- [ ] `id` из API сохранён в БД в поле `peer_id`

---
