#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ProjectX Bot — Deploy Script (Production Ready)
# Автоматическая установка и настройка бота на VPS (Ubuntu/Debian)
# ═══════════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PROJECT_NAME="projectx-bot"
# Исходная директория запуска
START_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Целевая безопасная директория для production-изоляции
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

check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Запустите от имени root: sudo bash deploy.sh"
    fi
}

check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        log "ОС: $PRETTY_NAME"
    fi
}

install_dependencies() {
    log "Обновление пакетов..."
    apt-get update -qq
    
    log "Установка системных зависимостей..."
    apt-get install -y -qq \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        git \
        curl \
        wget \
        sqlite3 \
        build-essential \
        cron \
        logrotate \
        > /dev/null 2>&1
    
    log "Создание системного пользователя projectx..."
    if ! id "projectx" &>/dev/null; then
        useradd -r -s /bin/false projectx
        success "Создан системный пользователь projectx"
    else
        warn "Системный пользователь projectx уже существует"
    fi
    
    success "Системные зависимости установлены"
}

migrate_to_opt() {
    if [ "$START_DIR" != "$PROJECT_DIR" ]; then
        log "Изоляция кодовой базы: перенос проекта в безопасную директорию $PROJECT_DIR..."
        mkdir -p "$PROJECT_DIR"
        # Копируем структуру проекта (включая скрытые файлы вроде .env, если есть)
        cp -r "$START_DIR"/. "$PROJECT_DIR/"
        success "Проект успешно перенесен в $PROJECT_DIR"
    fi
    cd "$PROJECT_DIR"
}

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
    
    log "Автоматическая генерация ключа шифрования базы данных (DB_ENCRYPTION_KEY)..."
    # Генерируем валидный url-safe base64 Fernet-совместимый ключ
    DB_ENCRYPTION_KEY=$(openssl rand -base64 32 | tr -d '\r\n')
    success "Ключ шифрования успешно сгенерирован"

    echo ""
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Настройка конфигурации бота${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    echo -e "${BLUE}[1/4]${NC} Telegram Bot Token (получить у @BotFather)"
    read -p "Введите BOT_TOKEN: " BOT_TOKEN
    [ -z "$BOT_TOKEN" ] && error "BOT_TOKEN не может быть пустым"
    
    echo ""
    echo -e "${BLUE}[2/4]${NC} Telegram ID администраторов (через запятую)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS
    [ -z "$ADMIN_IDS" ] && error "ADMIN_IDS не может быть пустым"
    
    echo ""
    echo -e "${BLUE}[3/4]${NC} Username поддержки (без знака @)"
    read -p "Введите SUPPORT_USERNAME [support]: " SUPPORT_USERNAME
    SUPPORT_USERNAME=${SUPPORT_USERNAME:-support}
    
    echo ""
    echo -e "${BLUE}[4/4]${NC} Бонус рефереру за первую оплату (в днях)"
    read -p "Введите REFERRAL_BONUS_DAYS [3]: " REFERRAL_BONUS_DAYS
    REFERRAL_BONUS_DAYS=${REFERRAL_BONUS_DAYS:-3}
    
    read -p "Лимит устройств по умолчанию [3]: " DEFAULT_DEVICE_LIMIT
    DEFAULT_DEVICE_LIMIT=${DEFAULT_DEVICE_LIMIT:-3}

    cat > "$PROJECT_DIR/.env" << EOF
# ProjectX Bot Configuration
# Создано автоматически: $(date)

BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
SUPPORT_USERNAME=$SUPPORT_USERNAME
REFERRAL_BONUS_DAYS=$REFERRAL_BONUS_DAYS
DEFAULT_DEVICE_LIMIT=$DEFAULT_DEVICE_LIMIT
DB_ENCRYPTION_KEY=$DB_ENCRYPTION_KEY
DB_PATH=./bot_data.db
EOF
    
    chmod 600 "$PROJECT_DIR/.env"
    success ".env файл создан и защищён"
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

init_database() {
    log "Инициализация асинхронной базы данных SQLite..."
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    python3 -c "
import asyncio
from database.connection import init_db
asyncio.run(init_db())
" 2>&1 | tee -a "$LOG_FILE"
    
    success "База данных успешно инициализирована"
}

setup_systemd() {
    log "Настройка и изоляция systemd сервиса..."
    
    systemctl is-active --quiet "$SERVICE_NAME" && systemctl stop "$SERVICE_NAME"
    
    # Сервис изолирован в /opt/projectx-bot, ProtectHome и ProtectSystem активны для максимального hardening'а безопасности
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

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    success "Systemd сервис успешно настроен и добавлен в автозапуск"
}

setup_backup() {
    log "Настройка регламентного автобэкапа базы данных..."
    
    mkdir -p "$BACKUP_DIR"
    
    cat > /usr/local/bin/projectx-backup.sh << EOF
#!/bin/bash
BACKUP_DIR="$BACKUP_DIR"
DB_FILE="$PROJECT_DIR/bot_data.db"
DATE=\$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="\$BACKUP_DIR/bot_data_\$DATE.db"

if [ -f "\$DB_FILE" ]; then
    sqlite3 "\$DB_FILE" ".backup '\$BACKUP_FILE'"
    gzip "\$BACKUP_FILE"
    find "\$BACKUP_DIR" -name "bot_data_*.db.gz" -mtime +30 -delete
    echo "[\$(date)] Backup completed: \${BACKUP_FILE}.gz"
fi
EOF
    
    chmod +x /usr/local/bin/projectx-backup.sh
    
    CRON_JOB="0 3 * * * /usr/local/bin/projectx-backup.sh >> /var/log/projectx-backup.log 2>&1"
    crontab -l 2>/dev/null | grep -v "projectx-backup" | crontab -
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    
    /usr/local/bin/projectx-backup.sh || true
    success "Автобэкап успешно настроен (ежедневно в 3:00, ротация 30 дней)"
}

setup_monitoring() {
    log "Настройка пятиминутного автовосстановления (Healthcheck)..."
    
    cat > /usr/local/bin/projectx-healthcheck.sh << EOF
#!/bin/bash
SERVICE_NAME="$SERVICE_NAME"
BOT_TOKEN_FILE="$PROJECT_DIR/.env"

ADMIN_IDS=\$(grep "^ADMIN_IDS=" "\$BOT_TOKEN_FILE" | cut -d'=' -f2 | cut -d',' -f1)
BOT_TOKEN=\$(grep "^BOT_TOKEN=" "\$BOT_TOKEN_FILE" | cut -d'=' -f2)

if ! systemctl is-active --quiet "\$SERVICE_NAME"; then
    systemctl restart "\$SERVICE_NAME"
    if [ -n "\$BOT_TOKEN" ] && [ -n "\$ADMIN_IDS" ]; then
        curl -s -X POST "https://api.telegram.org/bot\$BOT_TOKEN/sendMessage" \
            -d "chat_id=\$ADMIN_IDS" \
            -d "text=⚠️ Бот упал и был перезапущен автоматически (\$(date))" > /dev/null
    fi
    echo "[\$(date)] Bot crashed, self-healing triggered." >> /var/log/projectx-healthcheck.log
fi
EOF
    
    chmod +x /usr/local/bin/projectx-healthcheck.sh
    
    CRON_HEALTH="*/5 * * * * /usr/local/bin/projectx-healthcheck.sh"
    crontab -l 2>/dev/null | grep -v "projectx-healthcheck" | crontab -
    (crontab -l 2>/dev/null; echo "$CRON_HEALTH") | crontab -
    
    success "Мониторинг доступности настроен (проверка каждые 5 минут)"
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
    
    success "Ротация системных логов настроена"
}

start_bot() {
    log "Запуск службы бота..."
    systemctl start "$SERVICE_NAME"
    sleep 3
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Бот успешно запущен в фоновом режиме!"
        echo ""
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ProjectX Bot успешно развёрнут!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "📁 Директория:  ${BLUE}$PROJECT_DIR${NC}"
        echo -e "🔧 Статус:      ${BLUE}systemctl status $SERVICE_NAME${NC}"
        echo -e "📋 Логи:        ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
        echo -e "🔄 Перезапуск:  ${BLUE}systemctl restart $SERVICE_NAME${NC}"
        echo -e "💾 Бэкапы:      ${BLUE}$BACKUP_DIR${NC}"
        echo ""
    else
        error "Бот не смог запуститься. Изучите системные логи: journalctl -u $SERVICE_NAME -n 50"
    fi
}

show_status() {
    echo ""
    systemctl status "$SERVICE_NAME" --no-pager | head -20
}

main() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🚀 ProjectX Bot — Автоматический деплой (Production)${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    mkdir -p /var/log
    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"
    
    check_root
    check_os
    install_dependencies
    migrate_to_opt
    setup_env
    setup_venv
    init_database
    
    # Рекурсивно выдаем права пользователю projectx на изолированную директорию
    chown -R projectx:projectx "$PROJECT_DIR"
    chmod 600 "$PROJECT_DIR/.env"
    log "Конфиденциальные права на файлы обновлены для пользователя projectx"
    
    setup_systemd
    setup_backup
    setup_monitoring
    setup_logrotate
    start_bot
    show_status
    
    echo ""
    echo -e "${GREEN}✨ Деплой завершён успешно!${NC}"
}

case "${1:-}" in
    --uninstall)
        echo "Перенаправление на деинсталляцию..."
        if [ -f "./uninstall.sh" ]; then
            bash ./uninstall.sh
        else
            error "Файл uninstall.sh не найден в текущей директории."
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
        success "Бот остановлен"
        ;;
    --start)
        systemctl start "$SERVICE_NAME"
        show_status
        ;;
    --backup)
        /usr/local/bin/projectx-backup.sh
        ;;
    --help|-h)
        echo "Использование: $0 [опция]"
        echo ""
        echo "Без опций — полная автоматическая установка бота"
        echo ""
        echo "Опции:"
        echo "  --uninstall    Полное удаление системы"
        echo "  --status       Текущий статус systemd процесса"
        echo "  --logs         Просмотр логов в реальном времени"
        echo "  --restart      Перезапуск службы бота"
        echo "  --stop         Остановка службы бота"
        echo "  --start        Запуск службы бота"
        echo "  --backup       Принудительный ручной бэкап БД"
        echo "  --help         Справка"
        echo ""
        ;;
    *)
        main
        ;;
esac