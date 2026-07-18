from datetime import datetime, timezone

# Subscription
PERMANENT_SUBSCRIPTION_DAYS = 36500

# 🔥 ИСПРАВЛЕНО P2-10 + TZ-1: Унификация вечной даты + aware datetime
# Было: datetime(2100, 1, 1) (naive)
# Стало: datetime(2100, 1, 1, tzinfo=timezone.utc) (aware)
PERMANENT_END_DATE = datetime(2100, 1, 1, tzinfo=timezone.utc)

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
USER_CONTEXT_CACHE_TTL = 15.0

# Daily device creation limit (Spam protection)
DEVICE_DAILY_LIMIT = 25

# Self-Healing rate limit
SELF_HEALING_MAX_PER_CYCLE = 50