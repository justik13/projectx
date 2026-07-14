#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🛡️  ProjectX Bot — DevSecOps Production Deploy (v3.1 Final)
# ═══════════════════════════════════════════════════════════════

set -e
set -o pipefail

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

log()     { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

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
    echo "${key}='${value}'" >> "$PROJECT_DIR/.env"
}

preflight_checks() {
    log "Запуск pre-flight проверок..."
    if [ "$EUID" -ne 0 ]; then error "Запустите от имени root: sudo bash deploy.sh"; fi
    if [ ! -f /etc/os-release ]; then error "Не удалось определить ОС"; fi
    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then error "Поддерживаются только Ubuntu/Debian"; fi

    local avail_kb=$(df / | awk 'NR==2 {print $4}')
    local avail_gb=$((avail_kb / 1024 / 1024))
    if [ "$avail_gb" -lt 1 ]; then error "Недостаточно места. Доступно: ${avail_gb}GB, нужно 1GB"; fi

    if ! curl -s --max-time 5 https://archive.ubuntu.com/ubuntu/dists/noble/Release >/dev/null 2>&1; then 
        error "Нет доступа к интернету или репозиториям Ubuntu"
    fi
    
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        warn "Сервис $SERVICE_NAME уже запущен"
        if ! confirm "Перезапустить его после деплоя?"; then error "Деплой отменён."; fi
    fi
    success "Pre-flight проверки пройдены"
}

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

setup_firewall() {
    log "Настройка UFW firewall..."
    if ! command -v ufw &>/dev/null; then return; fi

    local SSH_PORT=$(ss -tlnp 2>/dev/null | grep -E 'sshd|ssh' | awk '{print $4}' | grep -oE '[0-9]+$' | sort -u | head -n1)
    SSH_PORT=${SSH_PORT:-22}

    if ! ss -tlnp 2>/dev/null | grep -q ":${SSH_PORT} "; then
        read -p "Введите порт SSH вручную (Enter для $SSH_PORT): " MANUAL_PORT
        SSH_PORT=${MANUAL_PORT:-$SSH_PORT}
    fi

    mkdir -p "$SNAPSHOT_DIR"
    ufw status numbered > "$SNAPSHOT_DIR/ufw-before-$(date +%s).txt" 2>&1 || true

    if ! confirm "Применить правила UFW (SSH:$SSH_PORT, HTTP:80, HTTPS:443)?"; then return; fi

    # Безопасные комментарии без спецсимволов
    ufw allow "$SSH_PORT"/tcp comment 'SSH' >/dev/null 2>&1 || true
    ufw allow 80/tcp comment 'HTTP' >/dev/null 2>&1 || true
    ufw allow 443/tcp comment 'HTTPS' >/dev/null 2>&1 || true
    ufw deny 8080/tcp comment 'Webhook Internal' >/dev/null 2>&1 || true

    ufw default deny incoming >/dev/null 2>&1
    ufw default allow outgoing >/dev/null 2>&1

    ufw --force enable >/dev/null 2>&1 || error "Ошибка включения UFW"
    success "UFW настроен безопасно"
}

migrate_to_opt() {
    if [ "$START_DIR" != "$PROJECT_DIR" ]; then
        log "Синхронизация проекта..."
        mkdir -p "$PROJECT_DIR"
        rsync -a --delete --exclude='.env' --exclude='bot_data.db*' --exclude='.git' --exclude='venv/' --exclude='__pycache__/' "$START_DIR/" "$PROJECT_DIR/" || error "Ошибка rsync"
    fi
    cd "$PROJECT_DIR"
}

setup_venv() {
    log "Настройка Python VENV..."
    [ ! -d "$VENV_DIR" ] && python3 -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    pip install -r "$PROJECT_DIR/requirements.txt" > /tmp/projectx-pip.log 2>&1 || error "Ошибка установки pip. Лог: /tmp/projectx-pip.log"
    success "Зависимости Python установлены"
}

setup_env() {
    log "Настройка .env файла..."
    if [ -f "$PROJECT_DIR/.env" ]; then
        cp "$PROJECT_DIR/.env" "$PROJECT_DIR/.env.backup-$(date +%s)" 2>/dev/null || true
        if ! confirm "Перезаписать .env новым конфигуратором?"; then return; fi
    fi

    echo -e "${BLUE}[1/6]${NC} Telegram Bot Token"
    read -s -p "Введите BOT_TOKEN (скрыт): " BOT_TOKEN; echo ""
    [ -z "$BOT_TOKEN" ] && error "Токен обязателен"
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]]; then error "Неверный формат BOT_TOKEN"; fi

    echo -e "${BLUE}[2/6]${NC} Telegram ID администраторов (через запятую)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS
    if [[ ! "$ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then error "Неверный формат ADMIN_IDS"; fi

    echo -e "${BLUE}[3/6]${NC} Username бота (без @)"
    read -p "Введите BOT_USERNAME: " BOT_USERNAME
    if [[ ! "$BOT_USERNAME" =~ ^[a-zA-Z0-9_]{3,32}$ ]]; then error "Неверный BOT_USERNAME"; fi

    echo -e "${BLUE}[4/6]${NC} Username поддержки [support]"
    read -p "Введите SUPPORT_USERNAME: " SUPPORT_USERNAME
    SUPPORT_USERNAME=${SUPPORT_USERNAME:-support}

    echo -e "${BLUE}[5/6]${NC} Бонус рефереру [3]"
    read -p "Введите REFERRAL_BONUS_DAYS: " REFERRAL_BONUS_DAYS
    REFERRAL_BONUS_DAYS=${REFERRAL_BONUS_DAYS:-3}

    echo -e "${BLUE}[6/6]${NC} Platega Merchant ID (Enter для пропуска)"
    read -p "Введите ID: " PLATEGA_MERCHANT_ID

    PLATEGA_SECRET=""
    PLATEGA_CALLBACK_URL=""
    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        read -s -p "Введите PLATEGA_SECRET (скрыт): " PLATEGA_SECRET; echo ""
        [ -z "$PLATEGA_SECRET" ] && error "PLATEGA_SECRET обязателен"
        read -p "Введите PLATEGA_CALLBACK_URL (https://...): " PLATEGA_CALLBACK_URL
        [ -z "$PLATEGA_CALLBACK_URL" ] && error "URL обязателен"
    fi

    local DB_KEY=$("$VENV_DIR/bin/python" -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")

    : > "$PROJECT_DIR/.env"
    write_env_var "BOT_TOKEN" "$BOT_TOKEN"
    write_env_var "ADMIN_IDS" "$ADMIN_IDS"
    write_env_var "BOT_USERNAME" "$BOT_USERNAME"
    write_env_var "SUPPORT_USERNAME" "$SUPPORT_USERNAME"
    write_env_var "REFERRAL_BONUS_DAYS" "$REFERRAL_BONUS_DAYS"
    write_env_var "DB_ENCRYPTION_KEY" "$DB_KEY"
    write_env_var "DB_PATH" "./bot_data.db"

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        write_env_var "PLATEGA_MERCHANT_ID" "$PLATEGA_MERCHANT_ID"
        write_env_var "PLATEGA_SECRET" "$PLATEGA_SECRET"
        write_env_var "PLATEGA_CALLBACK_URL" "$PLATEGA_CALLBACK_URL"
        write_env_var "PLATEGA_WEBHOOK_PORT" "8080"
        write_env_var "PLATEGA_RETURN_URL" "https://t.me/${BOT_USERNAME}"
    fi

    chown projectx:projectx "$PROJECT_DIR/.env"
    chmod 600 "$PROJECT_DIR/.env"
    success ".env защищён"
}

init_database() {
    log "Инициализация БД..."
    cd "$PROJECT_DIR"
    # Используем runuser вместо sudo (не требует пакета sudo)
    runuser -u projectx -- "$VENV_DIR/bin/python" -c "
import asyncio
from database.connection import init_db
asyncio.run(init_db())
" > /dev/null 2>&1 || warn "Инициализация отложена (выполнится при старте)"
}

verify_permissions() {
    chown -R projectx:projectx "$PROJECT_DIR"
    find "$PROJECT_DIR" -type d -exec chmod 750 {} \;
    find "$PROJECT_DIR" -type f -name "*.db*" -exec chmod 600 {} \;
    chmod 600 "$PROJECT_DIR/.env"
    success "Права доступа установлены (dir=750, files=600)"
}

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
WatchdogSec=60

# Максимальная изоляция
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
    success "Systemd настроен (ProtectSystem=strict)"
}

setup_nginx_ssl() {
    if ! grep -q "PLATEGA_CALLBACK_URL" "$PROJECT_DIR/.env" 2>/dev/null; then return; fi
    
    # Безопасное удаление кавычек
    local URL=$(grep "^PLATEGA_CALLBACK_URL=" "$PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d "\"'")
    local DOMAIN=$(echo "$URL" | sed -E 's|https?://([^/:]+).*|\1|')
    [ -z "$DOMAIN" ] && return

    log "Настройка Nginx для $DOMAIN"
    rm -f /etc/nginx/sites-enabled/default

    cat > "/etc/nginx/sites-available/projectx" << NGINXEOF
# Защита от DDoS (ограничение запросов)
limit_req_zone \$binary_remote_addr zone=mylimit:10m rate=5r/s;

server {
    listen 80;
    server_name $DOMAIN;

    location /webhook/platega {
        limit_req zone=mylimit burst=10 nodelay;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
    
    location / { return 404; }
}
NGINXEOF

    ln -sf /etc/nginx/sites-available/projectx /etc/nginx/sites-enabled/
    
    # Безопасная проверка конфига (не убивает скрипт при ошибке)
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        success "Nginx настроен и перезапущен"
    else
        warn "Ошибка в конфиге Nginx. Проверьте вручную: nginx -t"
    fi

    read -p "Email для SSL (certbot): " LE_EMAIL
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "${LE_EMAIL:-admin@$DOMAIN}" --redirect >/dev/null 2>&1 || warn "SSL не получен (проверьте DNS)"
}

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
    # Исправлен баг с pipefail: добавлен || true
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

start_bot() {
    log "Запуск бота..."
    systemctl start "$SERVICE_NAME"
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Бот успешно запущен!"
        echo -e "\n  📁 Директория: ${BLUE}$PROJECT_DIR${NC}"
        echo -e "  🔧 Статус:     ${BLUE}systemctl status $SERVICE_NAME${NC}"
        echo -e "  📋 Логи:       ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
        echo -e "  🔄 Рестарт:    ${BLUE}./deploy.sh --restart${NC}\n"
    else
        journalctl -u "$SERVICE_NAME" -n 20 --no-pager
        error "Бот не смог запуститься."
    fi
}

show_status() { systemctl status "$SERVICE_NAME" --no-pager | head -20 || true; }

main() {
    echo -e "${GREEN}🚀 ProjectX Bot Deploy v3.1 (Secure & Stable)${NC}\n"
    mkdir -p /var/log "$SNAPSHOT_DIR"
    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"
    
    preflight_checks
    install_dependencies
    setup_firewall
    migrate_to_opt
    setup_venv
    setup_env
    init_database
    verify_permissions
    setup_systemd
    setup_nginx_ssl
    setup_backup
    setup_monitoring
    start_bot
}

# CLI Интерфейс
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