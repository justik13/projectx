from datetime import datetime

# Subscription
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2100, 1, 1)

# Telegram limits
TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
BROADCAST_DELAY = 0.04

# Server limits
MAX_CLIENTS_HARD_LIMIT = 10000
DEFAULT_SUBNET_CAPACITY = 250

# Worker intervals (seconds)
TRAFFIC_SYNC_INTERVAL = 900
NOTIFICATION_INTERVAL = 1800
CLEANUP_INTERVAL = 86400
STALE_PAYMENT_THRESHOLD = 3600

# 🔥 НОВОЕ: Worker error recovery intervals
WORKER_ERROR_SLEEP_INTERVAL = 60  # Sleep после ошибки перед retry
WORKER_CRITICAL_ERROR_SLEEP = 300  # Sleep после критической ошибки (5 мин)

# API
API_CONCURRENCY_LIMIT = 20
API_RETRY_COUNT = 2
API_TIMEOUT = 15
AMNEZIA_PROTOCOL = "amneziawg2"

# Pagination
ITEMS_PER_PAGE = 10

# 🔥 НОВОЕ: Cache limits для предотвращения утечек памяти
HUB_CACHE_MAX_SIZE = 10000  # Максимум 10000 чатов в кэше
HUB_CACHE_TTL = 43200  # 12 часов (было 24 часа)
USER_CONTEXT_CACHE_MAX_SIZE = 2000  # Максимум 2000 пользователей
USER_CONTEXT_CACHE_TTL = 5.0  # 5 секунд (было 3 секунды)