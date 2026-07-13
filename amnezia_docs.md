# 📚 AMNEZIA WG 2.0 — ПОЛНАЯ ТЕХНИЧЕСКАЯ СПРАВКА

> Этот документ описывает **ТОЛЬКО протокол AmneziaWG 2.0 (AWG 2.0)**.
> Старые версии (AWG 1.0, 1.5) и другие протоколы (Xray, OpenVPN, IKEv2) **намеренно исключены**.
> Используется для Telegram-бота, работающего с `kyoresuas/amnezia-api`.

---

## 1. ТРИ ФОРМАТА КОНФИГУРАЦИИ (КРИТИЧНО!)

| Формат | Расширение | Для какого приложения | Содержимое |
|---|---|---|---|
| **AmneziaVPN native** | `.vpn` | **AmneziaVPN** (основной универсальный клиент) | Полный JSON с `containers`, `awg`, `last_config` |
| **AmneziaWG native** | `.conf` | **AmneziaWG** (отдельное легковесное приложение) | WireGuard INI + параметры обфускации AWG 2.0 |
| **vpn:// URI** | — (текст) | Оба приложения (импорт через QR/буфер) | `base64url(4-byte BE length + zlib(JSON))` — закодированный JSON из `.vpn` |

### ⚠️ Правило соответствия
- `.conf` файл **ДОЛЖЕН** содержать WireGuard INI (не JSON!)
- `.vpn` файл **ДОЛЖЕН** содержать полный JSON (не INI!)
- Если отдать `.conf` с JSON — **ни одно приложение не откроет**

---

## 2. ФОРМАТ `.vpn` (AmneziaVPN) — Полный JSON

Это то, что возвращает `kyoresuas/amnezia-api` в поле `client.config` как `vpn://...`.

```json
{
  "containers": [
    {
      "container": "amnesia-awg2",
      "awg": {
        "protocol_version": "2",
        "port": "1234",
        "transport_proto": "udp",
        "Jc": "4", "Jmin": "10", "Jmax": "50",
        "S1": "79", "S2": "115", "S3": "5", "S4": "1",
        "H1": "169154911-1234371153",
        "H2": "2057051984-2121122945",
        "H3": "2132872968-2133668229",
        "H4": "2136455412-2141801388",
        "I1": "<r 2><b 0x858000010001...>",
        "I2": "", "I3": "", "I4": "", "I5": "",
        "last_config": "{... JSON-строка ...}"
      }
    }
  ],
  "defaultContainer": "amnesia-awg2",
  "description": "Germany",
  "dns1": "1.1.1.1",
  "dns2": "1.0.0.1",
  "hostName": "server.example.com"
}
```

### 🔑 Ключевой момент: `last_config`
Поле `awg.last_config` содержит **JSON-строку** (не объект!), внутри которой есть поле `config` — **уже готовый WireGuard INI-файл как строка**.

Пример декодированного `last_config`:
```json
{
  "H1": "169154911-1234371153",
  "Jc": "4", "Jmin": "10", "Jmax": "50",
  "S1": "79", "S2": "115", "S3": "5", "S4": "1",
  "allowed_ips": ["0.0.0.0/0", "::/0"],
  "clientId": "dwvGfuluZKlNwickCgPb6DLiUE36icqZPiQWX/BHwBk=",
  "client_ip": "10.8.1.34",
  "client_priv_key": "uC6xUgdQDF4+fAOiw37ZQCG7XljilDsnBCl7VH7bAl8=",
  "client_pub_key": "dwvGfuluZKlNwickCgPb6DLiUE36icqZPiQWX/BHwBk=",
  "config": "[Interface]\nAddress = 10.8.1.34/32\n...",
  "hostName": "server.example.com",
  "mtu": "1376",
  "persistent_keep_alive": "25",
  "port": 1234,
  "psk_key": "PGh2rNsBmWVJC7qpa3fZ1dwB6tLjBUVKsxSZK6pMQRY=",
  "server_pub_key": "bRqF9LY7lnONibMDWH3u0QbeC7QbrLYPufdO4QMm53o="
}
```

---

## 3. ФОРМАТ `.conf` (AmneziaWG) — WireGuard INI + AWG 2.0

**СТРОГО ТАКОЙ ФОРМАТ** (из AmneziaWG-Architect и официальных клиентов):

```ini
[Interface]
Address = 10.8.1.34/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1376
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
Endpoint = server.example.com:1234
PersistentKeepalive = 25
```

### 🚨 КЛЮЧЕВЫЕ ОТЛИЧИЯ AWG 2.0 ОТ ОБЫЧНОГО WIREGUARD

| Параметр | AWG 2.0 | Как записывается в .conf |
|---|---|---|
| **Jc / Jmin / Jmax** | Обязательны | `Jc = 4`, `Jmin = 10`, `Jmax = 50` |
| **S1, S2, S3, S4** | Все четыре обязательны | `S1 = 79`, `S2 = 115`, `S3 = 5`, `S4 = 1` |
| **H1-H4** | **ДИАПАЗОНЫ через дефис** (не числа!) | `H1 = 169154911-1234371153` |
| **I1-I5 (CPS)** | В .conf файле → **`h1-h5` (lowercase!)** | `h1 = <r 2>...`, `h2 = `, ..., `h5 = ` |
| **MTU** | Рекомендовано 1376 | `MTU = 1376` |

### ⚠️ Регистр имеет значение!
- В JSON (`awg` секции): `I1`, `I2`, `I3`, `I4`, `I5` — **UPPERCASE**
- В `.conf` файле: **`h1`, `h2`, `h3`, `h4`, `h5`** — **lowercase**
- Это **пакеты инициализации (CPS)** — разные регистры для разных форматов!

---

## 4. ФОРМАТ `vpn://` URI

**Кодирование:**
```
vpn:// + base64url( 4-byte big-endian original_length + zlib_compressed_JSON )
```

**Декодирование:**
1. Убрать префикс `vpn://`
2. Заменить `-` на `+`, `_` на `/` (base64url → standard base64)
3. Добавить padding `=`
4. `base64.b64decode()`
5. Прочитать первые 4 байта как `struct.unpack(">I", ...)` — это длина оригинального JSON
6. `zlib.decompress(данные[4:])` → строка JSON
7. `json.loads()`

**Пример декодера на Python:**
```python
import base64, zlib, struct, json

def decode_vpn_uri(uri: str) -> dict:
    payload = uri[6:]  # убрать vpn://
    b64 = payload.replace("-", "+").replace("_", "/")
    b64 += "=" * ((4 - len(b64) % 4) % 4)
    data = base64.b64decode(b64)
    original_len = struct.unpack(">I", data[:4])[0]
    json_bytes = zlib.decompress(data[4:])
    assert len(json_bytes) == original_len
    return json.loads(json_bytes.decode("utf-8"))
```

---

## 5. ОГРАНИЧЕНИЯ ПАРАМЕТРОВ AWG 2.0

Из AmneziaWG-Architect (валидатор). **Нарушение = конфиг не работает!**

| Параметр | Ограничение |
|---|---|
| `S4` | `≤ 32` (Data prefix не более 32 байт) |
| `S3` | `≤ 64` (Cookie-ответ не более 64 байт) |
| `S1 + 56` | `≠ S2` (Init и Response не совпадают по длине) |
| `S2 + 92` | `≠ S3` (Response и Cookie не совпадают по длине) |
| `H1, H2, H3, H4` | **Диапазоны не должны пересекаться** (AWG 2.0) |
| `Jc` | `≥ 4` |
| `Jmax` | `> 81` |

---

## 6. AMNEZIA API (`kyoresuas/amnezia-api`) — ENDPOINTS

### Аутентификация
Все endpoints (кроме `/healthz`, `/metrics`, `/docs`) требуют заголовок:
```
x-api-key: <FASTIFY_API_KEY>
```

### Поддерживаемые протоколы
| Протокол | Значение `protocol` в запросах |
|---|---|
| AmneziaWG 2.0 | **`amneziawg2`** |
| ~~AmneziaWG~~ | ~~`amneziawg`~~ (не используется) |
| ~~Xray~~ | ~~`xray`~~ (не используется) |

### Основные endpoints

| Метод | Маршрут | Назначение |
|---|---|---|
| `POST` | `/clients` | Создать клиента, получить `vpn://` URI |
| `GET` | `/clients` | Список клиентов с трафиком и статусами |
| `PATCH` | `/clients` | Обновить статус/срок (`status: disabled`/`active`, `expiresAt`) |
| `DELETE` | `/clients` | Удалить клиента |
| `POST` | `/clients/qr` | Сгенерировать серию QR-кодов |
| `GET` | `/server` | Информация о сервере |
| `GET` | `/server/load` | Метрики (CPU/RAM/диск/сеть) |
| `GET` | `/healthz` | Healthcheck |

### Создание клиента (AWG 2.0)

**Запрос:**
```bash
curl -X POST http://<server>/clients \
-H "x-api-key: <KEY>" \
-H "Content-Type: application/json" \
-d '{
  "clientName": "device_name",
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
    "config": "vpn://AAAJBXjatVZZT-M6FP4r...",
    "protocol": "amneziawg2"
  }
}
```

### Пауза/возобновление доступа
`status: disabled` **отключает доступ, не удаляя ключ**. Клиенту не нужно пересоздавать конфиг.
```bash
curl -X PATCH http://<server>/clients \
-H "x-api-key: <KEY>" \
-d '{
  "clientId": "...",
  "protocol": "amneziawg2",
  "status": "disabled"
}'
```

### QR-коды
```bash
curl -X POST http://<server>/clients/qr \
-d '{"config": "vpn://..."}'
```
**Ответ:** массив PNG data URI. Большие конфиги разбиваются на несколько QR — клиент сканирует по очереди.

---

## 7. ЛОГИКА ГЕНЕРАЦИИ ФАЙЛОВ В БОТЕ

### Генерация `.vpn` (для AmneziaVPN)
```python
def build_vpn_file(vpn_uri: str) -> str:
    data = decode_vpn_uri(vpn_uri)  # полный JSON
    return json.dumps(data, indent=2, ensure_ascii=False)
```
Отдавать как `{device_name}.vpn`

### Генерация `.conf` (для AmneziaWG)
**ПРИОРИТЕТ №1:** Использовать готовый INI из `last_config.config`
```python
def build_conf_file(vpn_uri: str) -> str:
    data = decode_vpn_uri(vpn_uri)
    last_config_json = json.loads(data["containers"][0]["awg"]["last_config"])
    return last_config_json["config"]  # Уже готовый INI!
```

**ПРИОРИТЕТ №2 (fallback):** Собирать вручную, соблюдая формат:
- `[Interface]`: Address, DNS, MTU, PrivateKey
- `Jc/Jmin/Jmax` (обязательны для AWG 2.0)
- `S1-S4` (обязательны)
- `H1-H4` как строки-диапазоны
- Пустая строка
- **`h1-h5` (lowercase!)** для CPS (I1-I5 из JSON)
- Пустая строка
- `[Peer]`: PublicKey, PresharedKey, AllowedIPs, Endpoint, PersistentKeepalive

---

## 8. ПРАВИЛА ДЛЯ БОТА (ЖЁСТКИЕ)

1. **Никогда не отдавать `.conf` с JSON-содержимым** — приложение не откроет
2. **Никогда не отдавать `.vpn` с INI-содержимым** — приложение не откроет
3. **H1-H4 в AWG 2.0 — это диапазоны**, не одиночные числа
4. **В `.conf` файле CPS = `h1-h5` (lowercase)**, хотя в JSON это `I1-I5`
5. **Не трогать MTU** — использовать значение из `last_config.mtu` (обычно 1376)
6. **Всегда сохранять `vpn://` URI в БД** — это единственный источник истины
7. **При пересоздании устройства** — новый `vpn://`, новые ключи, новый `last_config`

---

## 9. ПРИМЕРЫ ПРИЛОЖЕНИЙ И ИХ ФОРМАТЫ

| Приложение | ОС | Формат импорта |
|---|---|---|
| **AmneziaVPN** | Win/Mac/Linux/Android/iOS | `.vpn` (JSON) или `vpn://` URI |
| **AmneziaWG** | Win/Mac/Linux/Android/iOS | `.conf` (WireGuard INI + AWG params) |
| **WireGuard (официальный)** | Все | `.conf` (только чистый WG, без AWG параметров) |

### Рекомендация для пользователя
- **По умолчанию** отдавать `.vpn` — работает в основном приложении AmneziaVPN
- **Опционально** `.conf` — для тех, кто использует отдельное приложение AmneziaWG

---

## 10. ССЫЛКИ

- API: https://github.com/kyoresuas/amnezia-api
- AmneziaVPN client: https://github.com/amnezia-vpn/amnezia-client
- AmneziaWG Windows: https://github.com/amnezia-vpn/amneziawg-windows-client
- AmneziaWG Apple: https://github.com/amnezia-vpn/amneziawg-apple
- AmneziaWG Android: https://github.com/amnezia-vpn/amneziawg-android
- AmneziaWG Architect (валидатор/генератор): https://github.com/Vadim-Khristenko/AmneziaWG-Architect
- Онлайн-генератор: https://architect.vai-rice.space/
```
