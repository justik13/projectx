from datetime import datetime

# Subscription
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2100, 1, 1)

# Telegram limits
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
BROADCAST_DELAY = 0.04

# Worker intervals (seconds)
TRAFFIC_SYNC_INTERVAL = 900
NOTIFICATION_INTERVAL = 1800
CLEANUP_INTERVAL = 86400
STALE_PAYMENT_THRESHOLD = 3600

# Worker initial delays (seconds)
WORKER_INITIAL_DELAY = 600
WORKER_ERROR_SLEEP_INTERVAL = 60
WORKER_CRITICAL_ERROR_SLEEP = 300

# API
API_CONCURRENCY_LIMIT = 20
API_RETRY_COUNT = 2
API_TIMEOUT = 15
AMNEZIA_PROTOCOL = "amneziawg2"

# Pagination
ITEMS_PER_PAGE = 10

# Cache limits
HUB_CACHE_MAX_SIZE = 10000
HUB_CACHE_TTL = 43200
USER_CONTEXT_CACHE_MAX_SIZE = 2000
# 🔥 ИСПРАВЛЕНО (Часть 2): TTL увеличен с 5с до 15с
# Было: 5.0 секунд — при 1000 пользователей генерировало ~200 req/sec к БД
# Стало: 15.0 секунд — нагрузка снижена в 3 раза
# Компромисс: пользователь не замечает задержку, но БД разгружена
USER_CONTEXT_CACHE_TTL = 15.0

# 🔥 ИСПРАВЛЕНО: Daily device creation limit (Spam protection)
DEVICE_DAILY_LIMIT = 25

# 🔥 ИСПРАВЛЕНО: Self-Healing rate limit
SELF_HEALING_MAX_PER_CYCLE = 50
