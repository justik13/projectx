#!/bin/bash

set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_NAME="projectx-bot"
START_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/opt/projectx-bot"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="projectx-bot"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
BACKUP_DIR="/root/backups/projectx"
LOG_FILE="/var/log/projectx-deploy.log"
SNAPSHOT_DIR="/root/.projectx-snapshots"
ROLLBACK_LOG="/var/log/projectx-rollback.log"

PG_PASS_FILE=""
REDIS_PASSWORD=""

log()     { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] [INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] [✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] [!]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] [✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

cleanup_temp_files() {
    if [[ -n "${PG_PASS_FILE:-}" ]]; then
        rm -f "$PG_PASS_FILE" 2>/dev/null || true
    fi
}

trap cleanup_temp_files EXIT INT TERM

confirm() {
    local message="$1"
    local default="${2:-N}"
    local prompt

    [[ "$default" =~ ^[Yy]$ ]] && prompt="(Y/n)" || prompt="(y/N)"

    echo ""
    read -p "$message $prompt: " response

    response=${response:-$default}

    [[ "$response" =~ ^[Yy]$ ]] && return 0 || return 1
}

write_env_var() {
    local key=$1
    local value=$2

    value="${value//\'/\'\\\'\'}"
    value="${value//\$/\\\$}"
    value="${value//\`/\\\`}"

    echo "${key}='${value}'" >> "$PROJECT_DIR/.env"
}

update_redis_env_if_exists() {
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        sed -i '/^REDIS_PASSWORD=/d' "$PROJECT_DIR/.env"
        sed -i '/^REDIS_URL=/d' "$PROJECT_DIR/.env"

        echo "REDIS_PASSWORD='${REDIS_PASSWORD}'" >> "$PROJECT_DIR/.env"
        echo "REDIS_URL='redis://:${REDIS_PASSWORD}@localhost:6379/0'" >> "$PROJECT_DIR/.env"

        chown projectx:projectx "$PROJECT_DIR/.env" 2>/dev/null || true
        chmod 600 "$PROJECT_DIR/.env"
    fi
}

rollback() {
    local step="$1"
    local error_msg="$2"

    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}" | tee -a "$ROLLBACK_LOG"
    echo -e "${RED}🚨 ROLLBACK TRIGGERED at step: $step${NC}" | tee -a "$ROLLBACK_LOG"
    echo -e "${RED}Error: $error_msg${NC}" | tee -a "$ROLLBACK_LOG"
    echo -e "${RED}═══════════════════════════════════════════════════════════════${NC}" | tee -a "$ROLLBACK_LOG"

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        log "Rollback: stopped $SERVICE_NAME"
    fi

    local env_backup
    env_backup=$(ls -t "$PROJECT_DIR/.env.backup-"* 2>/dev/null | head -n1 || true)

    if [[ -n "$env_backup" && -f "$env_backup" ]]; then
        cp "$env_backup" "$PROJECT_DIR/.env"
        log "Rollback: restored .env from $env_backup"
    fi

    local redis_backup
    redis_backup=$(ls -t /etc/redis/redis.conf.backup-* 2>/dev/null | head -n1 || true)

    if [[ -n "$redis_backup" && -f "$redis_backup" ]]; then
        cp "$redis_backup" /etc/redis/redis.conf 2>/dev/null || true
        systemctl restart redis-server 2>/dev/null || true
        log "Rollback: restored redis.conf from $redis_backup"
    fi

    error "Deploy failed. Check $ROLLBACK_LOG for details."
}

preflight_checks() {
    log "Запуск pre-flight проверок..."

    if [[ ! -f "$START_DIR/requirements.txt" ]]; then
        error "requirements.txt не найден в $START_DIR. Запустите скрипт из корня репозитория."
    fi

    if [ "$EUID" -ne 0 ]; then
        error "Запустите от имени root: sudo bash deploy.sh"
    fi

    if [ ! -f /etc/os-release ]; then
        error "Не удалось определить ОС"
    fi

    . /etc/os-release

    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        error "Поддерживаются только Ubuntu/Debian"
    fi

    local avail_kb
    avail_kb=$(df / | awk 'NR==2 {print $4}')
    local avail_gb=$((avail_kb / 1024 / 1024))

    if [ "$avail_gb" -lt 2 ]; then
        error "Недостаточно места. Доступно: ${avail_gb}GB, нужно 2GB"
    fi

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        warn "Сервис $SERVICE_NAME уже запущен"

        if ! confirm "Перезапустить его после деплоя?"; then
            error "Деплой отменён."
        fi
    fi

    success "Pre-flight проверки пройдены"
}

install_dependencies() {
    log "Установка системных зависимостей (PostgreSQL + Redis)..."

    apt-get update -qq || error "Не удалось обновить список пакетов"

    local install_log
    install_log=$(mktemp)

    if ! apt-get install -y \
        python3 python3-venv python3-pip python3-dev \
        git curl wget rsync build-essential cron logrotate \
        ufw nginx certbot python3-certbot-nginx \
        postgresql postgresql-contrib libpq-dev \
        redis-server \
        > "$install_log" 2>&1; then
        error "Ошибка apt. Лог: $install_log\n$(tail -20 "$install_log")"
    fi

    rm -f "$install_log"

    success "Системные зависимости установлены"

    if ! id "projectx" &>/dev/null; then
        useradd -r -s /bin/false -d /nonexistent projectx || error "Ошибка создания пользователя"
        success "Создан системный пользователь projectx"
    fi
}

setup_postgresql() {
    log "Настройка базы данных PostgreSQL..."

    if ! systemctl is-active --quiet postgresql; then
        systemctl start postgresql
        systemctl enable postgresql
    fi

    local PG_PORT
    PG_PORT=$(sudo -u postgres psql -tAc "SHOW port;" 2>/dev/null | tr -d '[:space:]')

    if [[ -z "$PG_PORT" || ! "$PG_PORT" =~ ^[0-9]+$ ]]; then
        PG_PORT=5432
        warn "Не удалось получить порт из PostgreSQL, предполагается стандартный: $PG_PORT"
    else
        log "PostgreSQL работает на порту: $PG_PORT"
    fi

    local wait_count=0

    while ! ss -tlnp | grep -qE ":${PG_PORT}\s"; do
        sleep 1
        wait_count=$((wait_count + 1))

        if [ $wait_count -ge 15 ]; then
            error "PostgreSQL не слушает порт $PG_PORT после 15 секунд ожидания. Проверьте: journalctl -u postgresql"
        fi
    done

    success "PostgreSQL запущен и слушает порт $PG_PORT"

    PG_PASS_FILE="$(mktemp /tmp/projectx_pg_pass.XXXXXX)"
    chmod 600 "$PG_PASS_FILE"

    if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='projectx_bot'" | grep -q 1; then
        warn "База данных projectx_bot уже существует. Пропускаем создание."

        read -s -p "Введите пароль от PostgreSQL (projectx): " DB_PASSWORD
        echo ""

        [ -z "$DB_PASSWORD" ] && error "Пароль не может быть пустым"

        printf '%s\n' "$DB_PASSWORD" > "$PG_PASS_FILE"

        return
    fi

    log "Создание пользователя и базы данных PostgreSQL..."

    read -s -p "Введите пароль для пользователя БД projectx: " DB_PASSWORD
    echo ""

    [ -z "$DB_PASSWORD" ] && error "Пароль не может быть пустым"

    if [[ ! "$DB_PASSWORD" =~ ^[a-zA-Z0-9_@#%^*+=-]{8,}$ ]]; then
        error "Пароль должен быть 8+ символов, только латиница/цифры/символы _@#%^*+=-"
    fi

    sudo -u postgres psql -v ON_ERROR_STOP=1 <<EOF
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'projectx') THEN
      CREATE USER projectx WITH PASSWORD '$DB_PASSWORD';
   END IF;
END
\$\$;

CREATE DATABASE projectx_bot OWNER projectx;
GRANT ALL PRIVILEGES ON DATABASE projectx_bot TO projectx;
EOF

    if [[ $? -ne 0 ]]; then
        error "Не удалось создать БД."
    fi

    success "Пользователь projectx и база projectx_bot созданы"

    printf '%s\n' "$DB_PASSWORD" > "$PG_PASS_FILE"
}

setup_redis() {
    log "Настройка Redis для FSM Storage..."

    if ! systemctl is-active --quiet redis-server; then
        systemctl start redis-server || true
        systemctl enable redis-server || true
    fi

    #
    # Если в существующем .env уже есть REDIS_PASSWORD, переиспользуем его.
    # Иначе генерируем новый пароль.
    #
    if [[ -f "$PROJECT_DIR/.env" ]]; then
        REDIS_PASSWORD=$(grep '^REDIS_PASSWORD=' "$PROJECT_DIR/.env" 2>/dev/null | head -n1 | cut -d'=' -f2- | tr -d '"' | tr -d "'" || true)
    fi

    if [[ -z "$REDIS_PASSWORD" ]]; then
        REDIS_PASSWORD=$(openssl rand -hex 32)
    fi

    local redis_conf="/etc/redis/redis.conf"

    if [[ -f "$redis_conf" ]]; then
        cp "$redis_conf" "$redis_conf.backup-$(date +%s)" 2>/dev/null || true

        sed -i '/^bind /d' "$redis_conf"
        sed -i '/^maxmemory /d' "$redis_conf"
        sed -i '/^maxmemory-policy /d' "$redis_conf"
        sed -i '/^save /d' "$redis_conf"
        sed -i '/^appendonly /d' "$redis_conf"
        sed -i '/^requirepass /d' "$redis_conf"

        echo "" >> "$redis_conf"
        echo "# === ProjectX Bot Config ===" >> "$redis_conf"
        echo "bind 127.0.0.1" >> "$redis_conf"
        echo "maxmemory 256mb" >> "$redis_conf"
        echo "maxmemory-policy allkeys-lru" >> "$redis_conf"
        echo 'save ""' >> "$redis_conf"
        echo "appendonly no" >> "$redis_conf"
        echo "requirepass $REDIS_PASSWORD" >> "$redis_conf"

        chown redis:redis "$redis_conf"
        chmod 640 "$redis_conf"

        if ! systemctl restart redis-server; then
            error "Redis restart failed."
        fi
    fi

    #
    # Если .env уже существует, сразу обновляем в нём Redis-пароль,
    # даже если позже пользователь откажется перезаписывать .env целиком.
    #
    update_redis_env_if_exists

    local redis_check=0

    for i in $(seq 1 10); do
        if redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q "PONG"; then
            redis_check=1
            break
        fi

        sleep 1
    done

    if [[ $redis_check -eq 0 ]]; then
        error "Redis не отвечает после настройки."
    fi

    success "Redis настроен, запущен и защищён паролем"
}

verify_infrastructure() {
    log "Финальная проверка доступности БД и Redis..."

    local PG_PORT
    PG_PORT=$(sudo -u postgres psql -tAc "SHOW port;" 2>/dev/null | tr -d '[:space:]')

    if [[ -z "$PG_PORT" ]]; then
        PG_PORT=5432
    fi

    if ! ss -tlnp | grep -qE ":${PG_PORT}\s"; then
        error "PostgreSQL не слушает порт $PG_PORT. Проверьте: journalctl -u postgresql"
    fi

    success "PostgreSQL слушает порт $PG_PORT"

    if ! redis-cli -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q "PONG"; then
        error "Redis не отвечает на ping."
    fi

    success "Redis полностью функционаен"
}

setup_firewall() {
    log "Настройка UFW firewall..."

    if ! command -v ufw &>/dev/null; then
        warn "UFW не установлен, пропуск"
        return
    fi

    local SSH_PORT=""

    if [ -f /etc/ssh/sshd_config ]; then
        SSH_PORT=$(grep -E "^Port " /etc/ssh/sshd_config | awk '{print $2}' | head -n1)
    fi

    if [[ -z "$SSH_PORT" || ! "$SSH_PORT" =~ ^[0-9]+$ ]]; then
        SSH_PORT=22
        warn "Не удалось определить порт SSH, используется: $SSH_PORT"
    fi

    mkdir -p "$SNAPSHOT_DIR"
    ufw status numbered > "$SNAPSHOT_DIR/ufw-before-$(date +%s).txt" 2>&1 || true

    if ! confirm "Применить правила UFW (SSH:$SSH_PORT, HTTP:80, HTTPS:443)?"; then
        return
    fi

    ufw allow "$SSH_PORT"/tcp comment 'SSH' >/dev/null 2>&1 || true
    ufw allow 80/tcp comment 'HTTP' >/dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS' >/dev/null 2>&1 || true

    ufw deny 8080/tcp comment 'Webhook Internal' >/dev/null 2>&1 || true
    ufw deny 6379/tcp comment 'Redis (blocked external)' >/dev/null 2>&1 || true

    ufw default deny incoming >/dev/null 2>&1
    ufw default allow outgoing >/dev/null 2>&1

    ufw --force enable >/dev/null 2>&1 || error "Ошибка включения UFW"

    success "UFW настроен безопасно"
}

migrate_to_opt() {
    if [ "$START_DIR" != "$PROJECT_DIR" ]; then
        log "Синхронизация проекта..."

        mkdir -p "$PROJECT_DIR"

        rsync -a --delete \
            --exclude='.env' \
            --exclude='*.db*' \
            --exclude='.git' \
            --exclude='venv/' \
            --exclude='__pycache__/' \
            "$START_DIR/" "$PROJECT_DIR/" || error "Ошибка rsync"
    fi

    cd "$PROJECT_DIR"
}

setup_venv() {
    log "Настройка Python VENV..."

    [ ! -d "$VENV_DIR" ] && python3 -m venv "$VENV_DIR"

    source "$VENV_DIR/bin/activate"

    pip install --upgrade pip setuptools wheel > /dev/null 2>&1

    local pip_log="$PROJECT_DIR/pip-install.log"

    if ! pip install -r "$PROJECT_DIR/requirements.txt" > "$pip_log" 2>&1; then
        error "Ошибка установки pip. Лог: $pip_log"
    fi

    success "Зависимости Python установлены"
}

setup_env() {
    log "Настройка .env файла..."

    if [ -f "$PROJECT_DIR/.env" ]; then
        cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env.backup-$(date +%s)" 2>/dev/null || true

        if ! confirm "Перезаписать .env новым конфигуратором?"; then
            return
        fi
    fi

    echo -e "${BLUE}[1/4]${NC} Telegram Bot Token"
    read -s -p "Введите BOT_TOKEN (скрыт): " BOT_TOKEN
    echo ""

    [ -z "$BOT_TOKEN" ] && error "Токен обязателен"

    echo -e "${BLUE}[2/4]${NC} Telegram ID администраторов (через запятую)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS

    echo -e "${BLUE}[3/4]${NC} Username поддержки [support]"
    read -p "Введите SUPPORT_USERNAME: " SUPPORT_USERNAME
    SUPPORT_USERNAME=${SUPPORT_USERNAME:-support}

    echo -e "${BLUE}[4/4]${NC} Platega Merchant ID (Enter для пропуска)"
    read -p "Введите ID: " PLATEGA_MERCHANT_ID

    PLATEGA_SECRET=""
    PLATEGA_CALLBACK_DOMAIN=""

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        read -s -p "Введите PLATEGA_SECRET (скрыт): " PLATEGA_SECRET
        echo ""

        [ -z "$PLATEGA_SECRET" ] && error "PLATEGA_SECRET обязателен"

        read -p "Введите домен для callback: " PLATEGA_CALLBACK_DOMAIN
        [ -z "$PLATEGA_CALLBACK_DOMAIN" ] && error "Домен обязателен"
    fi

    local EXISTING_KEY=""

    if [ -f "$PROJECT_DIR/.env" ]; then
        EXISTING_KEY=$(grep "^DB_ENCRYPTION_KEY=" "$PROJECT_DIR/.env" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    fi

    local DB_KEY

    if [ -n "$EXISTING_KEY" ]; then
        DB_KEY="$EXISTING_KEY"
    else
        DB_KEY=$("$VENV_DIR/bin/python" -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
    fi

    local DB_PASSWORD

    if [[ -n "${PG_PASS_FILE:-}" && -f "$PG_PASS_FILE" ]]; then
        DB_PASSWORD=$(cat "$PG_PASS_FILE")
        rm -f "$PG_PASS_FILE"
        PG_PASS_FILE=""
    else
        read -s -p "Введите пароль от PostgreSQL (projectx): " DB_PASSWORD
        echo ""
    fi

    local PG_PORT
    PG_PORT=$(sudo -u postgres psql -tAc "SHOW port;" 2>/dev/null | tr -d '[:space:]')

    if [[ -z "$PG_PORT" ]]; then
        PG_PORT=5432
    fi

    if [[ -z "${REDIS_PASSWORD:-}" ]]; then
        REDIS_PASSWORD=$(openssl rand -hex 32)
    fi

    : > "$PROJECT_DIR/.env"

    write_env_var "BOT_TOKEN" "$BOT_TOKEN"
    write_env_var "ADMIN_IDS" "$ADMIN_IDS"
    write_env_var "SUPPORT_USERNAME" "$SUPPORT_USERNAME"
    write_env_var "DB_ENCRYPTION_KEY" "$DB_KEY"

    local DB_PASSWORD_ENC
    DB_PASSWORD_ENC=$(printf '%s' "$DB_PASSWORD" | python3 -c 'import sys, urllib.parse; print(urllib.parse.quote_plus(sys.stdin.read()))')

    write_env_var "DATABASE_URL" "postgresql+asyncpg://projectx:${DB_PASSWORD_ENC}@localhost:${PG_PORT}/projectx_bot"

    write_env_var "REDIS_PASSWORD" "$REDIS_PASSWORD"
    write_env_var "REDIS_URL" "redis://:${REDIS_PASSWORD}@localhost:6379/0"

    #
    # Production-safe defaults для SSRF protection.
    #
    # Если Amnezia API находится локально, например:
    #   http://127.0.0.1:4001
    # то при необходимости можно вручную изменить:
    #   ALLOW_LOCAL_HTTP=true
    #
    # Для внешнего HTTPS API рекомендуется оставить:
    #   ALLOW_LOCAL_HTTP=false
    #   ALLOW_LOCAL_HTTPS=false
    #
    write_env_var "ALLOW_LOCAL_HTTP" "false"
    write_env_var "ALLOW_LOCAL_HTTPS" "false"

    write_env_var "REDIS_KEY_PREFIX" "projectx_bot:"

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        local CALLBACK_URL="https://${PLATEGA_CALLBACK_DOMAIN}/webhook/platega"

        write_env_var "PLATEGA_MERCHANT_ID" "$PLATEGA_MERCHANT_ID"
        write_env_var "PLATEGA_SECRET" "$PLATEGA_SECRET"
        write_env_var "PLATEGA_CALLBACK_URL" "$CALLBACK_URL"
        write_env_var "PLATEGA_WEBHOOK_PORT" "8080"
    fi

    chown projectx:projectx "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"

    success ".env защищён (Порт БД: $PG_PORT)"
}

verify_permissions() {
    chown -R projectx:projectx "$PROJECT_DIR"

    find "$PROJECT_DIR" -type d -exec chmod 750 {} \;
    find "$PROJECT_DIR" -type f -name "*.py" -exec chmod 640 {} \;
    find "$PROJECT_DIR" -type f -name "*.txt" -exec chmod 640 {} \;
    find "$PROJECT_DIR" -type f -name "*.sh" -exec chmod 750 {} \;

    if [ -d "$VENV_DIR/bin" ]; then
        find "$VENV_DIR/bin" -type f -exec chmod 750 {} \;
    fi

    chmod 600 "$PROJECT_DIR/.env"

    success "Права доступа установлены"
}

run_migrations() {
    log "Применение миграций Alembic..."

    cd "$PROJECT_DIR"

    if [[ ! -f "$PROJECT_DIR/alembic.ini" ]]; then
        warn "alembic.ini не найден. Пропускаем миграции."
        return 0
    fi

    if [[ ! -x "$VENV_DIR/bin/alembic" ]]; then
        warn "Alembic не установлен в venv. Пропускаем миграции."
        return 0
    fi

    if ! runuser -u projectx -- "$VENV_DIR/bin/alembic" upgrade head >> "$LOG_FILE" 2>&1; then
        warn "Ошибка применения миграций Alembic. Последние строки лога:"
        tail -n 50 "$LOG_FILE" || true
        return 1
    fi

    success "Миграции Alembic применены"
}

init_database() {
    log "Инициализация схемы БД PostgreSQL..."

    cd "$PROJECT_DIR"

    if ! runuser -u projectx -- "$VENV_DIR/bin/python" -c "
import asyncio
from database.connection import init_db

asyncio.run(init_db())
"; then
        warn "Ошибка инициализации БД"
        return 1
    fi

    success "БД инициализирована"
}

setup_systemd() {
    log "Настройка systemd сервиса..."

    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ProjectX Telegram Bot
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=projectx
Group=projectx
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$VENV_DIR/bin:/usr/bin"
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV_DIR/bin/python -m bot.main
Restart=always
RestartSec=10

ProtectSystem=strict
PrivateTmp=true
ProtectHome=true
NoNewPrivileges=true
ReadWritePaths=$PROJECT_DIR

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1

    success "Systemd настроен"
}

setup_nginx_ssl() {
    if ! grep -q "PLATEGA_CALLBACK_URL" "$PROJECT_DIR/.env" 2>/dev/null; then
        return
    fi

    local URL
    URL=$(grep "^PLATEGA_CALLBACK_URL=" "$PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'")

    local DOMAIN
    DOMAIN=$(echo "$URL" | sed -E 's|https?://([^/:]+).*|\1|')

    [ -z "$DOMAIN" ] && return

    local WEBHOOK_PORT
    WEBHOOK_PORT=$(grep "^PLATEGA_WEBHOOK_PORT=" "$PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'")
    WEBHOOK_PORT=${WEBHOOK_PORT:-8080}

    log "Настройка Nginx для $DOMAIN"

    rm -f /etc/nginx/sites-enabled/default

    cat > "/etc/nginx/sites-available/projectx" << NGINXEOF
limit_req_zone \$binary_remote_addr zone=mylimit:10m rate=10r/s;

server {
    listen 80;
    server_name $DOMAIN;

    location /webhook/platega {
        limit_req zone=mylimit burst=20 nodelay;

        proxy_pass http://127.0.0.1:${WEBHOOK_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;

        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;

        client_max_body_size 1m;
    }

    location / {
        return 404;
    }
}
NGINXEOF

    ln -sf /etc/nginx/sites-available/projectx /etc/nginx/sites-enabled/

    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        success "Nginx настроен"
    fi

    read -p "Email для SSL (certbot): " LE_EMAIL

    if ! timeout 120 certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "${LE_EMAIL:-admin@$DOMAIN}" --redirect >/dev/null 2>&1; then
        error "SSL не получен. Платёжный webhook нельзя считать готовым."
    fi

    success "SSL получен и Nginx переведён на HTTPS"
}

setup_backup() {
    log "Настройка бэкапов..."

    mkdir -p "$BACKUP_DIR"
    chown projectx:projectx "$BACKUP_DIR"

    cat > /usr/local/bin/projectx-backup.sh << 'EOF'
#!/bin/bash
set -euo pipefail

DIR="/root/backups/projectx"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p "$DIR"

if sudo -u postgres pg_dump -Fc projectx_bot | gzip > "$DIR/db_$DATE.sql.gz"; then
    echo "[$(date)] PostgreSQL backup created"
else
    echo "[$(date)] PostgreSQL backup FAILED" >&2
    exit 1
fi

cp /opt/projectx-bot/.env "$DIR/env_$DATE.bak"
gzip "$DIR/env_$DATE.bak"
chmod 600 "$DIR/env_$DATE.bak.gz" 2>/dev/null || true

find "$DIR" -type f -mtime +30 -delete
EOF

    chmod +x /usr/local/bin/projectx-backup.sh

    cat > /usr/local/bin/projectx-restore.sh << 'EOF'
#!/bin/bash
set -euo pipefail

DIR="/root/backups/projectx"
SERVICE_NAME="projectx-bot"
PROJECT_DIR="/opt/projectx-bot"

if [[ $# -lt 1 ]]; then
    echo "Использование: projectx-restore.sh <YYYYMMDD_HHMMSS>"
    echo ""
    echo "Доступные бэкапы:"
    ls -1 "$DIR"/db_*.sql.gz 2>/dev/null | sed -E 's#.*/db_([0-9_]+)\.sql\.gz#\1#' | sort -r || true
    exit 1
fi

STAMP="$1"

DB_FILE=$(ls -1 "$DIR"/db_${STAMP}*.sql.gz 2>/dev/null | head -n1 || true)
ENV_FILE=$(ls -1 "$DIR"/env_${STAMP}*.bak.gz 2>/dev/null | head -n1 || true)

if [[ -z "$DB_FILE" ]]; then
    echo "❌ Не найден бэкап БД для: $STAMP"
    exit 1
fi

echo "Будет восстановлено:"
echo "  DB:  $DB_FILE"
echo "  ENV: ${ENV_FILE:-не найден}"
echo ""

read -p "Продолжить восстановление? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Отменено"
    exit 0
fi

systemctl stop "$SERVICE_NAME" 2>/dev/null || true

echo "Terminating PostgreSQL connections..."
sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='projectx_bot' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true

echo "Restoring database..."
zcat "$DB_FILE" | sudo -u postgres pg_restore --clean --if-exists --dbname=projectx_bot

if [[ -n "$ENV_FILE" ]]; then
    echo "Restoring .env..."
    zcat "$ENV_FILE" > "$PROJECT_DIR/.env"
    chown projectx:projectx "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
fi

systemctl start "$SERVICE_NAME"

echo "✅ Restore completed"
EOF

    chmod +x /usr/local/bin/projectx-restore.sh

    (crontab -l 2>/dev/null | grep -v "projectx-backup" || true; echo "0 3 * * * /usr/local/bin/projectx-backup.sh") | crontab -

    success "Автобэкапы и restore-скрипт настроены"
}

setup_monitoring() {
    log "Настройка Healthcheck..."

    cat > /usr/local/bin/projectx-healthcheck.sh << 'EOF'
#!/bin/bash

CRASH_FILE="/opt/projectx-bot/.crash-count"
HEARTBEAT_FILE="/opt/projectx-bot/.heartbeat"
MAX_AGE=300

if [ "$(systemctl is-enabled projectx-bot 2>/dev/null)" = "enabled" ] && ! systemctl is-active --quiet projectx-bot; then
    COUNT=$(cat "$CRASH_FILE" 2>/dev/null || echo 0)

    if [ "$COUNT" -ge 5 ]; then
        exit 0
    fi

    systemctl start projectx-bot
    echo $((COUNT + 1)) > "$CRASH_FILE"
    exit 0
fi

if systemctl is-active --quiet projectx-bot; then
    NOW=$(date +%s)
    HB=""

    if [ -f "$HEARTBEAT_FILE" ]; then
        HB=$(awk '{print $1}' "$HEARTBEAT_FILE" 2>/dev/null)
    fi

    if [[ "$HB" =~ ^[0-9]+$ ]]; then
        AGE=$((NOW - HB))

        if [ "$AGE" -gt "$MAX_AGE" ]; then
            COUNT=$(cat "$CRASH_FILE" 2>/dev/null || echo 0)

            if [ "$COUNT" -ge 5 ]; then
                exit 0
            fi

            systemctl restart projectx-bot
            echo $((COUNT + 1)) > "$CRASH_FILE"
        else
            rm -f "$CRASH_FILE"
        fi
    else
        #
        # Если heartbeat-файл отсутствует или содержит некорректное значение,
        # считаем heartbeat stale и перезапускаем сервис.
        #
        COUNT=$(cat "$CRASH_FILE" 2>/dev/null || echo 0)

        if [ "$COUNT" -ge 5 ]; then
            exit 0
        fi

        systemctl restart projectx-bot
        echo $((COUNT + 1)) > "$CRASH_FILE"
    fi
fi
EOF

    chmod +x /usr/local/bin/projectx-healthcheck.sh

    (crontab -l 2>/dev/null | grep -v "projectx-healthcheck" || true; echo "*/5 * * * * /usr/local/bin/projectx-healthcheck.sh") | crontab -

    success "Healthcheck настроен"
}

start_bot() {
    log "Запуск бота..."

    systemctl start "$SERVICE_NAME"

    local wait_count=0
    local max_wait=30

    while [ $wait_count -lt $max_wait ]; do
        sleep 1

        if systemctl is-active --quiet "$SERVICE_NAME"; then
            success "Бот успешно запущен!"
            return 0
        fi

        wait_count=$((wait_count + 1))
    done

    journalctl -u "$SERVICE_NAME" -n 20 --no-pager

    rollback "start_bot" "Бот не смог запуститься"
}

main() {
    echo -e "${GREEN}🚀 ProjectX Bot Deploy v8.0 (Redis Auth + Restore + Hardened Healthcheck)${NC}\n"

    mkdir -p /var/log "$SNAPSHOT_DIR"

    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"

    preflight_checks      || rollback "preflight_checks" "Pre-flight failed"
    install_dependencies  || rollback "install_dependencies" "Dependencies failed"
    setup_postgresql      || rollback "setup_postgresql" "PostgreSQL failed"
    setup_redis           || rollback "setup_redis" "Redis failed"
    verify_infrastructure || rollback "verify_infrastructure" "Infrastructure check failed"
    setup_firewall        || rollback "setup_firewall" "Firewall failed"
    migrate_to_opt        || rollback "migrate_to_opt" "Sync failed"
    setup_venv            || rollback "setup_venv" "Venv failed"
    setup_env             || rollback "setup_env" "Env failed"
    verify_permissions    || rollback "verify_permissions" "Permissions failed"
    run_migrations        || rollback "run_migrations" "Alembic migrations failed"
    init_database         || rollback "init_database" "DB init failed"
    setup_systemd         || rollback "setup_systemd" "Systemd failed"
    setup_nginx_ssl       || rollback "setup_nginx_ssl" "Nginx failed"
    setup_backup          || rollback "setup_backup" "Backup failed"
    setup_monitoring      || rollback "setup_monitoring" "Monitoring failed"
    start_bot             || rollback "start_bot" "Startup failed"

    success "✨ Deploy completed successfully!"
}

case "${1:-}" in
    --status)
        systemctl status "$SERVICE_NAME" --no-pager | head -20
        ;;
    --logs)
        journalctl -u "$SERVICE_NAME" -f
        ;;
    --restart)
        systemctl restart "$SERVICE_NAME"
        ;;
    --stop)
        systemctl stop "$SERVICE_NAME"
        ;;
    --start)
        systemctl start "$SERVICE_NAME"
        ;;
    --backup)
        /usr/local/bin/projectx-backup.sh
        ;;
    --restore)
        /usr/local/bin/projectx-restore.sh "${2:-}"
        ;;
    --migrate)
        cd "$PROJECT_DIR"
        runuser -u projectx -- "$VENV_DIR/bin/alembic" upgrade head
        ;;
    --help|-h)
        echo "Использование: ./deploy.sh [--status|--logs|--restart|--stop|--start|--backup|--restore <stamp>|--migrate]"
        ;;
    *)
        main
        ;;
esac