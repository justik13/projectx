#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_NAME="projectx-bot"
START_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="/opt/projectx-bot"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="projectx-bot"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
BACKUP_DIR="/root/backups/projectx"
LOG_FILE="/var/log/projectx-deploy.log"

log() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

write_env_var() {
    local key=$1
    local value=$2
    value="${value//\'/\'\\\'\'}"
    echo "${key}='${value}'" >> "$PROJECT_DIR/.env"
}

check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Запустите от имени root: sudo bash deploy.sh"
    fi
}

check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log "ОС: $PRETTY_NAME"
        if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
            error "Скрипт поддерживает только Ubuntu и Debian. Обнаружена ОС: $ID"
        fi
    else
        error "Не удалось определить операционную систему (отсутствует /etc/os-release)."
    fi
}

install_dependencies() {
    log "Обновление пакетов..."
    apt-get update -qq

    log "Установка системных зависимостей..."
    if ! apt-get install -y -qq \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        git \
        curl \
        wget \
        sqlite3 \
        rsync \
        build-essential \
        cron \
        logrotate \
        ufw \
        nginx \
        certbot \
        python3-certbot-nginx \
        > /dev/null 2>&1; then
        error "Не удалось установить системные зависимости. Проверьте интернет и apt-репозитории."
    fi

    log "Создание системного пользователя projectx..."
    if ! id "projectx" &>/dev/null; then
        useradd -r -s /bin/false projectx
        success "Создан системный пользователь projectx"
    else
        warn "Системный пользователь projectx уже существует"
    fi

    success "Системные зависимости установлены"
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: Умное определение SSH-порта, защита от lockout
# ──────────────────────────────────────────────────────────────
setup_firewall() {
    log "Настройка UFW firewall..."

    ufw --force reset > /dev/null 2>&1

    # Автоматическое определение порта SSH из sshd_config
    SSH_PORT=$(grep -E '^Port ' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | head -n1)
    # Fallback: проверяем, на каком порту реально слушает sshd
    if [[ -z "$SSH_PORT" ]]; then
        SSH_PORT=$(ss -tlnp 2>/dev/null | grep -E ':22\b|sshd' | awk '{print $4}' | grep -oE '[0-9]+$' | head -n1)
    fi
    SSH_PORT=${SSH_PORT:-22}

    ufw allow "$SSH_PORT"/tcp comment 'SSH'
    log "SSH порт разрешён: $SSH_PORT"

    ufw allow 80/tcp comment 'HTTP (Nginx)'
    ufw allow 443/tcp comment 'HTTPS (Nginx + Let'\''s Encrypt)'

    # Порт 8080 блокируем снаружи — бот слушает только 127.0.0.1
    ufw deny 8080 comment 'Webhook (internal only)'

    ufw default deny incoming
    ufw default allow outgoing
    ufw --force enable

    success "UFW firewall настроен: SSH($SSH_PORT), HTTP(80), HTTPS(443)"
}

migrate_to_opt() {
    if [ "$START_DIR" != "$PROJECT_DIR" ]; then
        log "Изоляция кодовой базы: синхронизация проекта в $PROJECT_DIR..."
        mkdir -p "$PROJECT_DIR"

        if ! rsync -a --delete \
            --exclude='.env' \
            --exclude='bot_data.db' \
            --exclude='bot_data.db-wal' \
            --exclude='bot_data.db-shm' \
            --exclude='.git' \
            --exclude='venv/' \
            --exclude='__pycache__/' \
            "$START_DIR/" "$PROJECT_DIR/"; then
            error "Ошибка синхронизации файлов. Убедитесь, что rsync установлен."
        fi

        success "Проект успешно синхронизирован в $PROJECT_DIR"
    fi
    cd "$PROJECT_DIR"
}

setup_venv() {
    log "Настройка Python виртуального окружения..."

    if [ ! -d "$VENV_DIR" ]; then
        python3 -m venv "$VENV_DIR"
        success "Виртуальное окружение создано"
    else
        warn "Виртуальное окружение уже существует"
    fi

    source "$VENV_DIR/bin/activate"

    log "Обновление pip..."
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1

    if [ -f "$PROJECT_DIR/requirements.txt" ]; then
        log "Установка зависимостей проекта..."
        pip install -r "$PROJECT_DIR/requirements.txt" > /dev/null 2>&1
        success "Python зависимости установлены"
    else
        error "Файл requirements.txt не найден"
    fi
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: Убрано дублирование записи Platega переменных
# ──────────────────────────────────────────────────────────────
setup_env() {
    log "Настройка .env файла..."

    if [ -f "$PROJECT_DIR/.env" ]; then
        warn "Файл .env уже существует"
        read -p "Перезаписать его новым конфигуратором? (y/N): " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            success "Используется существующий .env"
            return
        fi
    fi

    echo ""
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Настройка конфигурации бота${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    echo -e "${BLUE}[1/6]${NC} Telegram Bot Token (получить у @BotFather)"
    read -p "Введите BOT_TOKEN: " BOT_TOKEN
    [ -z "$BOT_TOKEN" ] && error "BOT_TOKEN не может быть пустым"
    if [[ ! "$BOT_TOKEN" =~ ^[0-9]+:[a-zA-Z0-9_-]+$ ]]; then
        error "Неверный формат BOT_TOKEN. Ожидается формат 123456789:ABCdefGHI..."
    fi

    echo ""
    echo -e "${BLUE}[2/6]${NC} Telegram ID администраторов (через запятую)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS
    [ -z "$ADMIN_IDS" ] && error "ADMIN_IDS не может быть пустым"
    if [[ ! "$ADMIN_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        error "Неверный формат ADMIN_IDS. Ожидались числовые ID через запятую"
    fi

    echo ""
    echo -e "${BLUE}[3/6]${NC} Username бота (без знака @, для ссылок возврата)"
    read -p "Введите BOT_USERNAME: " BOT_USERNAME
    [ -z "$BOT_USERNAME" ] && error "BOT_USERNAME не может быть пустым"

    echo ""
    echo -e "${BLUE}[4/6]${NC} Username поддержки (без знака @)"
    read -p "Введите SUPPORT_USERNAME [support]: " SUPPORT_USERNAME
    SUPPORT_USERNAME=${SUPPORT_USERNAME:-support}

    echo ""
    echo -e "${BLUE}[5/6]${NC} Бонус рефереру за первую оплату (в днях)"
    read -p "Введите REFERRAL_BONUS_DAYS [3]: " REFERRAL_BONUS_DAYS
    REFERRAL_BONUS_DAYS=${REFERRAL_BONUS_DAYS:-3}

    echo ""
    echo -e "${BLUE}[6/6]${NC} Platega.io (для СБП, оставьте пустым если не нужно)"
    read -p "Введите PLATEGA_MERCHANT_ID: " PLATEGA_MERCHANT_ID

    PLATEGA_SECRET=""
    PLATEGA_CALLBACK_URL=""

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        echo ""
        read -p "Введите PLATEGA_SECRET: " PLATEGA_SECRET
        [ -z "$PLATEGA_SECRET" ] && error "PLATEGA_SECRET не может быть пустым если указан Merchant ID"

        echo ""
        read -p "Введите PLATEGA_CALLBACK_URL (https://yourdomain.com/webhook/platega): " PLATEGA_CALLBACK_URL
        [ -z "$PLATEGA_CALLBACK_URL" ] && error "PLATEGA_CALLBACK_URL не может быть пустым"
        if [[ ! "$PLATEGA_CALLBACK_URL" =~ ^https?:// ]]; then
            error "PLATEGA_CALLBACK_URL должен начинаться с http:// или https://"
        fi
        success "Platega.io данные собраны"
    else
        success "Platega.io пропущен (будут только Telegram Stars)"
    fi

    # Генерация ключа шифрования
    log "Генерация ключа шифрования базы данных (DB_ENCRYPTION_KEY)..."
    DB_ENCRYPTION_KEY=$("$VENV_DIR/bin/python" -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
    success "Ключ шифрования сгенерирован"

    # ── Запись .env (ОДИН блок, без дублирования) ──
    : > "$PROJECT_DIR/.env"

    write_env_var "BOT_TOKEN" "$BOT_TOKEN"
    write_env_var "ADMIN_IDS" "$ADMIN_IDS"
    write_env_var "BOT_USERNAME" "$BOT_USERNAME"
    write_env_var "SUPPORT_USERNAME" "$SUPPORT_USERNAME"
    write_env_var "REFERRAL_BONUS_DAYS" "$REFERRAL_BONUS_DAYS"
    write_env_var "DB_ENCRYPTION_KEY" "$DB_ENCRYPTION_KEY"
    write_env_var "DB_PATH" "./bot_data.db"

    if [ -n "$PLATEGA_MERCHANT_ID" ]; then
        write_env_var "PLATEGA_MERCHANT_ID" "$PLATEGA_MERCHANT_ID"
        write_env_var "PLATEGA_SECRET" "$PLATEGA_SECRET"
        write_env_var "PLATEGA_BASE_URL" "https://app.platega.io"
        write_env_var "PLATEGA_CALLBACK_URL" "$PLATEGA_CALLBACK_URL"
        write_env_var "PLATEGA_WEBHOOK_PORT" "8080"
        write_env_var "PLATEGA_PAYMENT_METHOD" "2"
        write_env_var "PLATEGA_RETURN_URL" "https://t.me/${BOT_USERNAME}"
        write_env_var "PLATEGA_FAILED_URL" "https://t.me/${BOT_USERNAME}"
    fi

    chmod 600 "$PROJECT_DIR/.env"
    success ".env файл создан и защищён (права 600)"
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: Прямой вызов venv python без source
# ──────────────────────────────────────────────────────────────
init_database() {
    log "Инициализация базы данных SQLite..."
    cd "$PROJECT_DIR"

    "$VENV_DIR/bin/python" -c "
import asyncio
from database.connection import init_db
asyncio.run(init_db())
" 2>&1 | tee -a "$LOG_FILE"

    success "База данных успешно инициализирована"
}

# ──────────────────────────────────────────────────────────────
# 🔥 НОВОЕ: Проверка и исправление прав доступа
# ──────────────────────────────────────────────────────────────
verify_permissions() {
    log "Проверка прав доступа к критическим файлам..."

    chown -R projectx:projectx "$PROJECT_DIR"

    if [ -f "$PROJECT_DIR/.env" ]; then
        chmod 600 "$PROJECT_DIR/.env"
    fi

    if [ -f "$PROJECT_DIR/bot_data.db" ]; then
        chmod 600 "$PROJECT_DIR/bot_data.db"
        chown projectx:projectx "$PROJECT_DIR/bot_data.db"
        # WAL и SHM файлы тоже защищаем
        [ -f "$PROJECT_DIR/bot_data.db-wal" ] && chmod 600 "$PROJECT_DIR/bot_data.db-wal" && chown projectx:projectx "$PROJECT_DIR/bot_data.db-wal"
        [ -f "$PROJECT_DIR/bot_data.db-shm" ] && chmod 600 "$PROJECT_DIR/bot_data.db-shm" && chown projectx:projectx "$PROJECT_DIR/bot_data.db-shm"
    fi

    chmod 700 "$PROJECT_DIR"

    success "Права доступа установлены: dir=700, .env=600, db=600, owner=projectx"
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: ProtectSystem=full, ReadWritePaths=/dev/shm,
#    убран MemoryDenyWriteExecute (крашит Python/cryptography)
# ──────────────────────────────────────────────────────────────
setup_systemd() {
    log "Настройка systemd сервиса с изоляцией..."
    systemctl is-active --quiet "$SERVICE_NAME" && systemctl stop "$SERVICE_NAME" || true

    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ProjectX Telegram Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=projectx
Group=projectx
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV_DIR/bin/python -m bot.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# ── Security Hardening (безопасно для Python) ──
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=$PROJECT_DIR
ReadWritePaths=/dev/shm

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    success "Systemd сервис настроен с изоляцией (ProtectSystem=full)"
}

# ──────────────────────────────────────────────────────────────
# 🔥 НОВОЕ: Nginx reverse proxy + Let's Encrypt SSL
# ──────────────────────────────────────────────────────────────
setup_nginx_ssl() {
    log "Проверка необходимости настройки Nginx + SSL..."

    if ! grep -q "PLATEGA_CALLBACK_URL" "$PROJECT_DIR/.env" 2>/dev/null; then
        warn "Platega не настроен — пропускаем Nginx/SSL"
        return
    fi

    CALLBACK_URL=$(grep "^PLATEGA_CALLBACK_URL=" "$PROJECT_DIR/.env" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
    DOMAIN=$(echo "$CALLBACK_URL" | sed -E 's|https?://([^/:]+).*|\1|')

    if [[ -z "$DOMAIN" || "$DOMAIN" == "localhost" || "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        warn "Домен для SSL не определён ($DOMAIN). Пропускаем."
        return
    fi

    log "Настройка Nginx для домена: $DOMAIN"

    # Запрашиваем email для Let's Encrypt
    read -p "Введите Email для Let's Encrypt [admin@$DOMAIN]: " LE_EMAIL
    LE_EMAIL=${LE_EMAIL:-"admin@$DOMAIN"}

    # Удаляем дефолтный сайт Nginx, чтобы не перехватывал трафик
    rm -f /etc/nginx/sites-enabled/default

    cat > "/etc/nginx/sites-available/projectx" << NGINXEOF
server {
    listen 80;
    server_name $DOMAIN;

    client_max_body_size 1M;

    location /webhook/platega {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
        proxy_connect_timeout 10s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
    }

    location / {
        return 404;
    }
}
NGINXEOF

    ln -sf /etc/nginx/sites-available/projectx /etc/nginx/sites-enabled/

    if nginx -t 2>/dev/null; then
        systemctl reload nginx
        success "Nginx конфиг валиден и перезапущен"
    else
        warn "Nginx конфиг невалиден! Проверьте вручную: nginx -t"
        return
    fi

    log "Получение SSL сертификата через Let's Encrypt..."
    if certbot --nginx -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$LE_EMAIL" \
        --redirect 2>&1 | tee -a "$LOG_FILE"; then
        success "SSL сертификат получен для $DOMAIN"
    else
        warn "Не удалось получить SSL. Проверьте DNS A-запись для $DOMAIN → IP сервера"
    fi

    systemctl enable certbot.timer 2>/dev/null || true
    systemctl start certbot.timer 2>/dev/null || true
}

setup_backup() {
    log "Настройка автобэкапа базы данных..."

    mkdir -p "$BACKUP_DIR"
    chown projectx:projectx "$BACKUP_DIR"

    cat > /usr/local/bin/projectx-backup.sh << 'BACKUPEOF'
#!/bin/bash
BACKUPEOF

    cat >> /usr/local/bin/projectx-backup.sh << BACKUPEOF
BACKUP_DIR="$BACKUP_DIR"
DB_FILE="$PROJECT_DIR/bot_data.db"
ENV_FILE="$PROJECT_DIR/.env"
DATE=\$(date +%Y%m%d_%H%M%S)

if [ -f "\$DB_FILE" ]; then
    sqlite3 "\$DB_FILE" ".backup '\$BACKUP_DIR/bot_data_\$DATE.db'"
    gzip "\$BACKUP_DIR/bot_data_\$DATE.db"

    if [ -f "\$ENV_FILE" ]; then
        cp "\$ENV_FILE" "\$BACKUP_DIR/env_\$DATE.backup"
        gzip "\$BACKUP_DIR/env_\$DATE.backup"
    fi

    find "\$BACKUP_DIR" -name "bot_data_*.db.gz" -mtime +30 -delete
    find "\$BACKUP_DIR" -name "env_*.backup.gz" -mtime +30 -delete

    echo "[\$(date)] Backup completed successfully."
else
    echo "[\$(date)] DB file not found, skipping."
fi
BACKUPEOF

    chmod +x /usr/local/bin/projectx-backup.sh

    CRON_JOB="0 3 * * * /usr/local/bin/projectx-backup.sh >> /var/log/projectx-backup.log 2>&1"
    (crontab -l 2>/dev/null | grep -v "projectx-backup" || true; echo "$CRON_JOB") | crontab -

    /usr/local/bin/projectx-backup.sh || true

    success "Автобэкап настроен (ежедневно в 3:00, ротация 30 дней)"
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: Healthcheck проверяет is-enabled,
#    чтобы не перезапускать бот после намеренного --stop
# ──────────────────────────────────────────────────────────────
setup_monitoring() {
    log "Настройка healthcheck (автовосстановление каждые 5 минут)..."

    cat > /usr/local/bin/projectx-healthcheck.sh << 'HEALTHEOF'
#!/bin/bash
HEALTHEOF

    cat >> /usr/local/bin/projectx-healthcheck.sh << HEALTHEOF
SERVICE_NAME="$SERVICE_NAME"
BOT_TOKEN_FILE="$PROJECT_DIR/.env"

# Перезапускаем ТОЛЬКО если сервис включён в автозагрузку
# (после deploy.sh --stop сервис disabled и не будет перезапущен)
if [ "\$(systemctl is-enabled "\$SERVICE_NAME" 2>/dev/null)" = "enabled" ] && \\
   ! systemctl is-active --quiet "\$SERVICE_NAME"; then

    systemctl start "\$SERVICE_NAME"

    ADMIN_IDS=\$(grep "^ADMIN_IDS=" "\$BOT_TOKEN_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'" | cut -d',' -f1)
    BOT_TOKEN=\$(grep "^BOT_TOKEN=" "\$BOT_TOKEN_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")

    if [ -n "\$BOT_TOKEN" ] && [ -n "\$ADMIN_IDS" ]; then
        curl -s -X POST "https://api.telegram.org/bot\$BOT_TOKEN/sendMessage" \\
            -d "chat_id=\$ADMIN_IDS" \\
            -d "text=⚠️ Бот упал и был перезапущен автоматически (\$(date '+%Y-%m-%d %H:%M:%S'))" > /dev/null 2>&1
    fi

    echo "[\$(date)] Bot crashed, self-healing triggered." >> /var/log/projectx-healthcheck.log
fi
HEALTHEOF

    chmod +x /usr/local/bin/projectx-healthcheck.sh

    CRON_HEALTH="*/5 * * * * /usr/local/bin/projectx-healthcheck.sh"
    (crontab -l 2>/dev/null | grep -v "projectx-healthcheck" || true; echo "$CRON_HEALTH") | crontab -

    success "Healthcheck настроен (каждые 5 мин, respects --stop)"
}

setup_logrotate() {
    log "Настройка ротации лог-файлов..."

    cat > /etc/logrotate.d/projectx << EOF
/var/log/projectx-*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}
EOF

    success "Ротация логов настроена (7 дней)"
}

start_bot() {
    log "Запуск службы бота..."
    systemctl start "$SERVICE_NAME"
    sleep 3

    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Бот успешно запущен!"
        echo ""
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  🚀 ProjectX Bot — развёрнут и работает${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "  📁 Директория:   ${BLUE}$PROJECT_DIR${NC}"
        echo -e "  🔧 Статус:       ${BLUE}systemctl status $SERVICE_NAME${NC}"
        echo -e "  📋 Логи:         ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
        echo -e "  🔄 Перезапуск:   ${BLUE}deploy.sh --restart${NC}"
        echo -e "  ⏹  Остановка:    ${BLUE}deploy.sh --stop${NC}"
        echo -e "  💾 Бэкапы:       ${BLUE}$BACKUP_DIR${NC}"
        echo ""
        echo -e "${YELLOW}  ⚠️  Не забудьте проверить bot/main.py:${NC}"
        echo -e "${YELLOW}     web.TCPSite(runner, \"127.0.0.1\", port)${NC}"
        echo ""
    else
        error "Бот не смог запуститься. Логи: journalctl -u $SERVICE_NAME -n 50 --no-pager"
    fi
}

show_status() {
    echo ""
    systemctl status "$SERVICE_NAME" --no-pager | head -20 || true
}

main() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🚀 ProjectX Bot — Production Deploy${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""

    mkdir -p /var/log
    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"

    check_root
    check_os
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
    setup_logrotate
    start_bot
    show_status

    echo ""
    echo -e "${GREEN}✨ Деплой завершён успешно!${NC}"
}

# ──────────────────────────────────────────────────────────────
# 🔥 ИСПРАВЛЕНО: --stop делает disable (healthcheck не разбудит),
#    --start делает enable + start
# ──────────────────────────────────────────────────────────────
case "${1:-}" in
    --uninstall)
        if [ -f "./uninstall.sh" ]; then
            bash ./uninstall.sh
        else
            error "Файл uninstall.sh не найден."
        fi
        ;;
    --status)
        show_status
        ;;
    --logs)
        journalctl -u "$SERVICE_NAME" -f
        ;;
    --restart)
        systemctl restart "$SERVICE_NAME"
        show_status
        ;;
    --stop)
        systemctl stop "$SERVICE_NAME"
        systemctl disable "$SERVICE_NAME"
        success "Бот остановлен и отключён от автозагрузки (healthcheck не перезапустит)"
        ;;
    --start)
        systemctl enable "$SERVICE_NAME"
        systemctl start "$SERVICE_NAME"
        show_status
        ;;
    --backup)
        /usr/local/bin/projectx-backup.sh
        ;;
    --firewall-status)
        ufw status verbose
        ;;
    --help|-h)
        echo "Использование: $0 [опция]"
        echo ""
        echo "Без опций — полная автоматическая установка"
        echo ""
        echo "Опции:"
        echo "  --uninstall       Полное удаление (запускает uninstall.sh)"
        echo "  --status          Статус systemd сервиса"
        echo "  --logs            Логи в реальном времени"
        echo "  --restart         Перезапуск бота"
        echo "  --stop            Остановка + disable (healthcheck не разбудит)"
        echo "  --start           Enable + запуск бота"
        echo "  --backup          Ручной бэкап БД"
        echo "  --firewall-status Показать правила UFW"
        echo "  --help            Эта справка"
        ;;
    *)
        main
        ;;
esac