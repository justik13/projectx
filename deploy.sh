#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🛡️  ProjectX Bot — DevSecOps Production Deploy (v4.1 Secure)
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
# LOGGING & UTILITIES
# ═══════════════════════════════════════════════════════════════
log()     { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] [INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] [✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] [!]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] [✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

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

# ═══════════════════════════════════════════════════════════════
# ROLLBACK MECHANISM
# ═══════════════════════════════════════════════════════════════
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

    local env_backup=$(ls -t "$PROJECT_DIR/.env.backup-"* 2>/dev/null | head -n1)
    if [[ -n "$env_backup" && -f "$env_backup" ]]; then
        cp "$env_backup" "$PROJECT_DIR/.env"
        log "Rollback: restored .env from $env_backup"
    fi

    local ufw_snapshot=$(ls -t "$SNAPSHOT_DIR/ufw-before-"*.txt 2>/dev/null | head -n1)
    if [[ -n "$ufw_snapshot" && -f "$ufw_snapshot" ]]; then
        warn "Rollback: UFW snapshot available at $ufw_snapshot"
        warn "Manual restore required: ufw reset && cat $ufw_snapshot | ufw add"
    fi

    error "Deploy failed. Check $ROLLBACK_LOG for details."
}

# ═══════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECKS
# ═══════════════════════════════════════════════════════════════
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

    local avail_kb=$(df / | awk 'NR==2 {print $4}')
    local avail_gb=$((avail_kb / 1024 / 1024))
    if [ "$avail_gb" -lt 1 ]; then
        error "Недостаточно места. Доступно: ${avail_gb}GB, нужно 1GB"
    fi

    if ! curl -s --max-time 10 ${http_proxy+--proxy "$http_proxy"} https://archive.ubuntu.com/ubuntu/dists/noble/Release >/dev/null 2>&1; then
        error "Нет доступа к интернету или репозиториям Ubuntu"
    fi

    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        warn "Сервис $SERVICE_NAME уже запущен"
        if ! confirm "Перезапустить его после деплоя?"; then
            error "Деплой отменён."
        fi
    fi

    success "Pre-flight проверки пройдены"
}

# ═══════════════════════════════════════════════════════════════
# SYSTEM DEPENDENCIES
# ═══════════════════════════════════════════════════════════════
install_dependencies() {
    log "Установка системных зависимостей (1-3 минуты)..."
    apt-get update -qq || error "Не удалось обновить список пакетов"

    local install_log=$(mktemp)
    if ! apt-get install -y python3 python3-venv python3-pip python3-dev git curl wget sqlite3 rsync build-essential cron logrotate ufw nginx certbot python3-certbot-nginx > "$install_log" 2>&1; then
        error "Ошибка apt. Лог: $install_log\n$(tail -20 "$install_log")"
    fi
    rm -f "$install_log"

    success "Системные зависимости установлены"

    if ! id "projectx" &>/dev/null; then
        useradd -r -s /bin/false -d /nonexistent projectx || error "Ошибка создания пользователя"
        success "Создан системный пользователь projectx"
    fi
}

# ═══════════════════════════════════════════════════════════════
# FIREWALL
# ═══════════════════════════════════════════════════════════════
setup_firewall() {
    log "Настройка UFW firewall..."

    if ! command -v ufw &>/dev/null; then
        warn "UFW не установлен, пропуск настройки firewall"
        return
    fi

    local SSH_PORT=""
    if command -v ss &>/dev/null; then
        SSH_PORT=$(ss -tlnp 2>/dev/null | grep -E 'sshd|ssh' | awk '{print $4}' | grep -oE ':[0-9]+$' | grep -oE '[0-9]+' | sort -u | head -n1)
    fi
    if [[ -z "$SSH_PORT" || ! "$SSH_PORT" =~ ^[0-9]+$ ]]; then
        SSH_PORT=22
        warn "Не удалось определить порт SSH, используется стандартный: $SSH_PORT"
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

    ufw default deny incoming >/dev/null 2>&1
    ufw default allow outgoing >/dev/null 2>&1
    ufw --force enable >/dev/null 2>&1 || error "Ошибка включения UFW"

    success "UFW настроен безопасно"
}

# ═══════════════════════════════════════════════════════════════
# PROJECT SYNC
# ═══════════════════════════════════════════════════════════════
migrate_to_opt() {
    if [ "$START_DIR" != "$PROJECT_DIR" ]; then
        log "Синхронизация проекта..."
        mkdir -p "$PROJECT_DIR"
        rsync -a --delete --exclude='.env' --exclude='bot_data.db*' --exclude='.git' --exclude='venv/' --exclude='__pycache__/' "$START_DIR/" "$PROJECT_DIR/" || error "Ошибка rsync"
    fi
    cd "$PROJECT_DIR"
}

# ═══════════════════════════════════════════════════════════════
# PYTHON VENV
# ═══════════════════════════════════════════════════════════════
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

# ═══════════════════════════════════════════════════════════════
# ENVIRONMENT CONFIG
# ═══════════════════════════════════════════════════════════════
setup_env() {
    log "Настройка .env файла..."

    if [ -f "$PROJECT_DIR/.env" ]; then
        cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env.backup-$(date +%s)" 2>/dev/null || true
        if ! confirm "Перезаписать .env новым конфигуратором?"; then
            return
        fi
    fi

    echo -e "${BLUE}[1/4]${NC} Telegram Bot Token"
    read -s -p "Введите BOT_TOKEN (скрыт): " BOT_TOKEN; echo ""
    [ -z "$BOT_TOKEN" ] && error "Токен обязателен"
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]]; then
        error "Неверный формат BOT_TOKEN"
    fi

    echo -e "${BLUE}[2/4]${NC} Telegram ID администраторов (через запятую)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS
    if [[ ! "$ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        error "Неверный формат ADMIN_IDS"
    fi

    echo -e "${BLUE}[3/4]${NC} Username поддержки [support]"
    read -p "Введите SUPPORT_USERNAME: " SUPPORT_USERNAME
    SUPPORT_USERNAME=${SUPPORT_USERNAME:-support}

    echo -e "${BLUE}[4/4]${NC} Platega Merchant ID (Enter для пропуска)"
    read -p "Введите ID: " PLATEGA_MERCHANT_ID
    PLATEGA_SECRET=""
    PLATEGA_CALLBACK_DOMAIN=""

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        read -s -p "Введите PLATEGA_SECRET (скрыт): " PLATEGA_SECRET; echo ""
        [ -z "$PLATEGA_SECRET" ] && error "PLATEGA_SECRET обязателен"
        read -p "Введите домен для callback (например: just1kbot.1337.cx): " PLATEGA_CALLBACK_DOMAIN
        [ -z "$PLATEGA_CALLBACK_DOMAIN" ] && error "Домен обязателен"
    fi

    local DB_KEY=$("$VENV_DIR/bin/python" -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")

    : > "$PROJECT_DIR/.env"
    write_env_var "BOT_TOKEN" "$BOT_TOKEN"
    write_env_var "ADMIN_IDS" "$ADMIN_IDS"
    write_env_var "SUPPORT_USERNAME" "$SUPPORT_USERNAME"
    write_env_var "DB_ENCRYPTION_KEY" "$DB_KEY"
    write_env_var "DB_PATH" "./bot_data.db"

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        local CALLBACK_URL="https://${PLATEGA_CALLBACK_DOMAIN}/webhook/platega"
        write_env_var "PLATEGA_MERCHANT_ID" "$PLATEGA_MERCHANT_ID"
        write_env_var "PLATEGA_SECRET" "$PLATEGA_SECRET"
        write_env_var "PLATEGA_CALLBACK_URL" "$CALLBACK_URL"
        write_env_var "PLATEGA_WEBHOOK_PORT" "8080"
        write_env_var "PLATEGA_RETURN_URL" "https://t.me/placeholder"
    fi

    chown projectx:projectx "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    success ".env защищён"
}

# ═══════════════════════════════════════════════════════════════
# PERMISSIONS & DATABASE
# ═══════════════════════════════════════════════════════════════
verify_permissions() {
    chown -R projectx:projectx "$PROJECT_DIR"
    find "$PROJECT_DIR" -type d -exec chmod 750 {} \;
    find "$PROJECT_DIR" -type f -name "*.db*" -exec chmod 600 {} \;
    chmod 600 "$PROJECT_DIR/.env"
    success "Права доступа установлены (dir=750, files=600)"
}

init_database() {
    log "Инициализация БД..."
    cd "$PROJECT_DIR"
    runuser -u projectx -- "$VENV_DIR/bin/python" -c "
import asyncio
from database.connection import init_db
asyncio.run(init_db())
" > /dev/null 2>&1 || warn "Инициализация отложена (выполнится при старте)"
}

# ═══════════════════════════════════════════════════════════════
# SYSTEMD SERVICE
# ═══════════════════════════════════════════════════════════════
setup_systemd() {
    log "Настройка systemd сервиса..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true

    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ProjectX Telegram Bot
After=network.target

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
WatchdogSec=300
ProtectSystem=strict
PrivateTmp=true
ProtectHome=true
NoNewPrivileges=true
ReadWritePaths=$PROJECT_DIR
ReadWritePaths=/dev/shm

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    success "Systemd настроен (ProtectSystem=strict, WatchdogSec=300)"
}

# ═══════════════════════════════════════════════════════════════
# NGINX + SSL
# ═══════════════════════════════════════════════════════════════
setup_nginx_ssl() {
    if ! grep -q "PLATEGA_CALLBACK_URL" "$PROJECT_DIR/.env" 2>/dev/null; then
        return
    fi

    local URL=$(grep "^PLATEGA_CALLBACK_URL=" "$PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'")
    local DOMAIN=$(echo "$URL" | sed -E 's|https?://([^/:]+).*|\1|')
    [ -z "$DOMAIN" ] && return

    log "Настройка Nginx для $DOMAIN"

    rm -f /etc/nginx/sites-enabled/default

    cat > "/etc/nginx/sites-available/projectx" << NGINXEOF
limit_req_zone \$binary_remote_addr zone=mylimit:10m rate=10r/s;

server {
    listen 80;
    server_name $DOMAIN;

    location /webhook/platega {
        limit_req zone=mylimit burst=20 nodelay;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
    }

    location / { return 404; }
}
NGINXEOF

    ln -sf /etc/nginx/sites-available/projectx /etc/nginx/sites-enabled/

    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        success "Nginx настроен и перезапущен"
    else
        warn "Ошибка в конфиге Nginx. Проверьте вручную: nginx -t"
    fi

    read -p "Email для SSL (certbot): " LE_EMAIL

    if ! timeout 120 certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "${LE_EMAIL:-admin@$DOMAIN}" --redirect >/dev/null 2>&1; then
        warn "SSL не получен (проверьте DNS или таймаут)"
    fi
}

# ═══════════════════════════════════════════════════════════════
# BACKUP & MONITORING
# ═══════════════════════════════════════════════════════════════
setup_backup() {
    log "Настройка бэкапов..."
    mkdir -p "$BACKUP_DIR"
    chown projectx:projectx "$BACKUP_DIR"

    cat > /usr/local/bin/projectx-backup.sh << 'EOF'
#!/bin/bash
DIR="/root/backups/projectx"
DATE=$(date +%Y%m%d_%H%M%S)
sqlite3 /opt/projectx-bot/bot_data.db ".backup '$DIR/db_$DATE.db'" && gzip "$DIR/db_$DATE.db"
cp /opt/projectx-bot/.env "$DIR/env_$DATE.bak" && gzip "$DIR/env_$DATE.bak"
find "$DIR" -type f -mtime +30 -delete
EOF
    chmod +x /usr/local/bin/projectx-backup.sh

    (crontab -l 2>/dev/null | grep -v "projectx-backup" || true; echo "0 3 * * * /usr/local/bin/projectx-backup.sh") | crontab -
    success "Автобэкапы настроены"
}

setup_monitoring() {
    log "Настройка Healthcheck..."

    cat > /usr/local/bin/projectx-healthcheck.sh << 'EOF'
#!/bin/bash
CRASH_FILE="/opt/projectx-bot/.crash-count"
if [ "$(systemctl is-enabled projectx-bot 2>/dev/null)" = "enabled" ] && ! systemctl is-active --quiet projectx-bot; then
    COUNT=$(cat "$CRASH_FILE" 2>/dev/null || echo 0)
    if [ "$COUNT" -ge 5 ]; then exit 0; fi
    systemctl start projectx-bot
    echo $((COUNT + 1)) > "$CRASH_FILE"
    chown projectx:projectx "$CRASH_FILE" 2>/dev/null
else
    rm -f "$CRASH_FILE"
fi
EOF
    chmod +x /usr/local/bin/projectx-healthcheck.sh

    (crontab -l 2>/dev/null | grep -v "projectx-healthcheck" || true; echo "*/5 * * * * /usr/local/bin/projectx-healthcheck.sh") | crontab -
    success "Healthcheck настроен"
}

# ═══════════════════════════════════════════════════════════════
# START BOT
# ═══════════════════════════════════════════════════════════════
start_bot() {
    log "Запуск бота..."
    systemctl start "$SERVICE_NAME"

    local wait_count=0
    local max_wait=10
    while [ $wait_count -lt $max_wait ]; do
        sleep 1
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            success "Бот успешно запущен!"
            echo -e "\n📁 Директория: ${BLUE}$PROJECT_DIR${NC}"
            echo -e "  🔧 Статус:     ${BLUE}systemctl status $SERVICE_NAME${NC}"
            echo -e "  📋 Логи:       ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
            echo -e "  🔄 Рестарт:    ${BLUE}./deploy.sh --restart${NC}\n"
            return 0
        fi
        wait_count=$((wait_count + 1))
    done

    journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    rollback "start_bot" "Бот не смог запуститься за ${max_wait} секунд"
}

show_status() {
    systemctl status "$SERVICE_NAME" --no-pager | head -20 || true
}

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
main() {
    echo -e "${GREEN}🚀 ProjectX Bot Deploy v4.1 (Secure & Stable)${NC}\n"
    mkdir -p /var/log "$SNAPSHOT_DIR"
    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"

    preflight_checks || rollback "preflight_checks" "Pre-flight checks failed"
    install_dependencies || rollback "install_dependencies" "Failed to install dependencies"
    setup_firewall || rollback "setup_firewall" "Firewall setup failed"
    migrate_to_opt || rollback "migrate_to_opt" "Project sync failed"
    setup_venv || rollback "setup_venv" "Python venv setup failed"
    setup_env || rollback "setup_env" "Environment config failed"

    verify_permissions || rollback "verify_permissions" "Permissions setup failed"
    init_database || rollback "init_database" "Database initialization failed"

    setup_systemd || rollback "setup_systemd" "Systemd setup failed"
    setup_nginx_ssl || rollback "setup_nginx_ssl" "Nginx/SSL setup failed"
    setup_backup || rollback "setup_backup" "Backup setup failed"
    setup_monitoring || rollback "setup_monitoring" "Monitoring setup failed"
    start_bot || rollback "start_bot" "Bot startup failed"

    success "✨ Deploy completed successfully!"
}

# ═══════════════════════════════════════════════════════════════
# CLI INTERFACE
# ═══════════════════════════════════════════════════════════════
case "${1:-}" in
    --status) show_status ;;
    --logs) journalctl -u "$SERVICE_NAME" -f ;;
    --restart) systemctl restart "$SERVICE_NAME"; show_status ;;
    --stop) systemctl stop "$SERVICE_NAME"; systemctl disable "$SERVICE_NAME"; success "Бот остановлен" ;;
    --start) systemctl enable "$SERVICE_NAME"; systemctl start "$SERVICE_NAME"; show_status ;;
    --backup) /usr/local/bin/projectx-backup.sh; success "Бэкап создан" ;;
    --help|-h) echo "Использование: ./deploy.sh [--status|--logs|--restart|--stop|--start|--backup]" ;;
    *) main ;;
esac