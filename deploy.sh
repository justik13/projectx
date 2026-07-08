#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ProjectX Bot — Deploy Script
# Автоматическая установка и настройка бота на VPS (Ubuntu/Debian)
# ═══════════════════════════════════════════════════════════════

set -e  # Остановка при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Переменные
PROJECT_NAME="projectx-bot"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="projectx-bot"
SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
BACKUP_DIR="/root/backups/projectx"
LOG_FILE="/var/log/projectx-deploy.log"

# Функции логирования
log() { echo -e "${BLUE}[INFO]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error() { echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }

# Проверка root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Этот скрипт нужно запускать от имени root: sudo bash deploy.sh"
    fi
}

# Проверка ОС
check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
            warn "Скрипт оптимизирован для Ubuntu/Debian. Другие ОС могут работать некорректно."
        fi
        log "ОС: $PRETTY_NAME"
    fi
}

# Установка системных зависимостей
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
    
    success "Системные зависимости установлены"
}

# Настройка .env файла
setup_env() {
    log "Настройка .env файла..."
    
    if [ -f "$PROJECT_DIR/.env" ]; then
        warn "Файл .env уже существует"
        read -p "Перезаписать его? (y/N): " overwrite
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
    
    # BOT_TOKEN
    echo -e "${BLUE}[1/4]${NC} Telegram Bot Token"
    echo "Получить у @BotFather в Telegram"
    read -p "Введите BOT_TOKEN: " BOT_TOKEN
    if [ -z "$BOT_TOKEN" ]; then
        error "BOT_TOKEN не может быть пустым"
    fi
    
    # ADMIN_IDS
    echo ""
    echo -e "${BLUE}[2/4]${NC} Telegram ID администраторов"
    echo "Узнать свой ID: написать @userinfobot"
    echo "Можно указать несколько ID через запятую (например: 123456,789012)"
    read -p "Введите ADMIN_IDS: " ADMIN_IDS
    if [ -z "$ADMIN_IDS" ]; then
        error "ADMIN_IDS не может быть пустым"
    fi
    
    # SUPPORT_USERNAME
    echo ""
    echo -e "${BLUE}[3/4]${NC} Username поддержки"
    echo "Без символа @ (например: just1k13)"
    read -p "Введите SUPPORT_USERNAME: " SUPPORT_USERNAME
    if [ -z "$SUPPORT_USERNAME" ]; then
        warn "SUPPORT_USERNAME не указан, используется 'support'"
        SUPPORT_USERNAME="support"
    fi
    
    # REFERRAL_BONUS_DAYS
    echo ""
    echo -e "${BLUE}[4/4]${NC} Бонус рефереру за первую оплату (в днях)"
    read -p "Введите REFERRAL_BONUS_DAYS [3]: " REFERRAL_BONUS_DAYS
    REFERRAL_BONUS_DAYS=${REFERRAL_BONUS_DAYS:-3}
    
    # DEFAULT_DEVICE_LIMIT
    echo ""
    read -p "Лимит устройств по умолчанию [3]: " DEFAULT_DEVICE_LIMIT
    DEFAULT_DEVICE_LIMIT=${DEFAULT_DEVICE_LIMIT:-3}
    
    # Создаём .env
    cat > "$PROJECT_DIR/.env" << EOF
# ProjectX Bot Configuration
# Создано автоматически: $(date)

BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
SUPPORT_USERNAME=$SUPPORT_USERNAME
REFERRAL_BONUS_DAYS=$REFERRAL_BONUS_DAYS
DEFAULT_DEVICE_LIMIT=$DEFAULT_DEVICE_LIMIT
DATABASE_URL=sqlite+aiosqlite:///./bot_data.db
EOF
    
    chmod 600 "$PROJECT_DIR/.env"
    success ".env файл создан и защищён (chmod 600)"
}

# Настройка виртуального окружения
setup_venv() {
    log "Настройка Python виртуального окружения..."
    
    if [ -d "$VENV_DIR" ]; then
        warn "Виртуальное окружение уже существует"
    else
        python3 -m venv "$VENV_DIR"
        success "Виртуальное окружение создано"
    fi
    
    source "$VENV_DIR/bin/activate"
    
    log "Обновление pip..."
    pip install --upgrade pip setuptools wheel > /dev/null 2>&1
    
    if [ -f "$PROJECT_DIR/requirements.txt" ]; then
        log "Установка зависимостей из requirements.txt..."
        pip install -r "$PROJECT_DIR/requirements.txt" > /dev/null 2>&1
        success "Python зависимости установлены"
    else
        error "Файл requirements.txt не найден"
    fi
}

# Инициализация базы данных
init_database() {
    log "Инициализация базы данных..."
    
    cd "$PROJECT_DIR"
    source "$VENV_DIR/bin/activate"
    
    python3 -m bot.main --init-only 2>/dev/null || {
        # Если --init-only не поддерживается, просто создаём БД
        python3 -c "
import asyncio
from database.connection import init_db
asyncio.run(init_db())
print('База данных инициализирована')
" 2>&1 | tee -a "$LOG_FILE"
    }
    
    success "База данных готова"
}

# Создание systemd сервиса
setup_systemd() {
    log "Настройка systemd сервиса..."
    
    # Останавливаем старый сервис если есть
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl stop "$SERVICE_NAME"
    fi
    
    # Создаём unit-файл
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=ProjectX Telegram Bot
Documentation=https://github.com/justik13/projectx
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$VENV_DIR/bin/python -m bot.main
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

# Безопасность
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$PROJECT_DIR
ReadWritePaths=$BACKUP_DIR

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"
    
    success "Systemd сервис настроен и включён в автозапуск"
}

# Настройка автобэкапа
setup_backup() {
    log "Настройка автобэкапа базы данных..."
    
    mkdir -p "$BACKUP_DIR"
    
    # Создаём скрипт бэкапа
    cat > /usr/local/bin/projectx-backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/root/backups/projectx"
PROJECT_DIR="PROJECT_DIR_PLACEHOLDER"
DB_FILE="$PROJECT_DIR/bot_data.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/bot_data_$DATE.db"

# Создаём бэкап
if [ -f "$DB_FILE" ]; then
    sqlite3 "$DB_FILE" ".backup '$BACKUP_FILE'"
    
    # Сжимаем
    gzip "$BACKUP_FILE"
    
    # Удаляем бэкапы старше 30 дней
    find "$BACKUP_DIR" -name "bot_data_*.db.gz" -mtime +30 -delete
    
    echo "[$(date)] Backup created: ${BACKUP_FILE}.gz"
fi
EOF
    
    # Заменяем placeholder на реальный путь
    sed -i "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" /usr/local/bin/projectx-backup.sh
    chmod +x /usr/local/bin/projectx-backup.sh
    
    # Добавляем в cron (каждый день в 3:00)
    CRON_JOB="0 3 * * * /usr/local/bin/projectx-backup.sh >> /var/log/projectx-backup.log 2>&1"
    
    # Удаляем старую запись если есть
    crontab -l 2>/dev/null | grep -v "projectx-backup" | crontab -
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    
    # Создаём первый бэкап
    /usr/local/bin/projectx-backup.sh || true
    
    success "Автобэкап настроен (ежедневно в 3:00, хранение 30 дней)"
}

# Настройка мониторинга
setup_monitoring() {
    log "Настройка автовосстановления при падении..."
    
    cat > /usr/local/bin/projectx-healthcheck.sh << 'EOF'
#!/bin/bash
SERVICE_NAME="projectx-bot"
PROJECT_DIR="PROJECT_DIR_PLACEHOLDER"
BOT_TOKEN_FILE="$PROJECT_DIR/.env"

# Получаем ADMIN_IDS из .env
ADMIN_IDS=$(grep "^ADMIN_IDS=" "$BOT_TOKEN_FILE" | cut -d'=' -f2 | cut -d',' -f1)
BOT_TOKEN=$(grep "^BOT_TOKEN=" "$BOT_TOKEN_FILE" | cut -d'=' -f2)

# Проверяем статус сервиса
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl restart "$SERVICE_NAME"
    
    # Уведомляем админа
    if [ -n "$BOT_TOKEN" ] && [ -n "$ADMIN_IDS" ]; then
        curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
            -d "chat_id=$ADMIN_IDS" \
            -d "text=⚠️ Бот упал и был перезапущен автоматически ($(date))" > /dev/null
    fi
    
    echo "[$(date)] Bot restarted automatically" >> /var/log/projectx-healthcheck.log
fi
EOF
    
    sed -i "s|PROJECT_DIR_PLACEHOLDER|$PROJECT_DIR|g" /usr/local/bin/projectx-healthcheck.sh
    chmod +x /usr/local/bin/projectx-healthcheck.sh
    
    # Healthcheck каждые 5 минут
    CRON_HEALTH="*/5 * * * * /usr/local/bin/projectx-healthcheck.sh"
    crontab -l 2>/dev/null | grep -v "projectx-healthcheck" | crontab -
    (crontab -l 2>/dev/null; echo "$CRON_HEALTH") | crontab -
    
    success "Мониторинг настроен (каждые 5 минут)"
}

# Настройка logrotate
setup_logrotate() {
    log "Настройка ротации логов..."
    
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
    
    success "Ротация логов настроена"
}

# Запуск бота
start_bot() {
    log "Запуск бота..."
    
    systemctl start "$SERVICE_NAME"
    sleep 3
    
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        success "Бот успешно запущен!"
        echo ""
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ProjectX Bot успешно развёрнут!${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "📁 Проект:        ${BLUE}$PROJECT_DIR${NC}"
        echo -e "🔧 Сервис:        ${BLUE}systemctl status $SERVICE_NAME${NC}"
        echo -e "📋 Логи:          ${BLUE}journalctl -u $SERVICE_NAME -f${NC}"
        echo -e "🔄 Перезапуск:    ${BLUE}systemctl restart $SERVICE_NAME${NC}"
        echo -e "⏹  Остановка:     ${BLUE}systemctl stop $SERVICE_NAME${NC}"
        echo -e "💾 Бэкапы:        ${BLUE}$BACKUP_DIR${NC}"
        echo ""
        echo -e "${YELLOW}Полезные команды:${NC}"
        echo -e "  ${BLUE}systemctl status $SERVICE_NAME${NC}    — статус бота"
        echo -e "  ${BLUE}journalctl -u $SERVICE_NAME -n 100${NC}  — последние 100 строк логов"
        echo -e "  ${BLUE}/usr/local/bin/projectx-backup.sh${NC}   — ручной бэкап БД"
        echo ""
    else
        error "Бот не запустился. Проверьте логи: journalctl -u $SERVICE_NAME -n 50"
    fi
}

# Показ статуса
show_status() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    systemctl status "$SERVICE_NAME" --no-pager | head -20
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
}

# ═══════════════════════════════════════════════════════════════
# Главный сценарий
# ═══════════════════════════════════════════════════════════════

main() {
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🚀 ProjectX Bot — Автоматический деплой${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Инициализация лог-файла
    mkdir -p /var/log
    echo "=== Deploy started: $(date) ===" > "$LOG_FILE"
    
    check_root
    check_os
    install_dependencies
    setup_env
    setup_venv
    init_database
    setup_systemd
    setup_backup
    setup_monitoring
    setup_logrotate
    start_bot
    show_status
    
    echo ""
    echo -e "${GREEN}✨ Деплой завершён успешно! Полный лог: $LOG_FILE${NC}"
    echo ""
}

# Обработка аргументов
case "${1:-}" in
    --uninstall)
        echo "Удаление ProjectX Bot..."
        systemctl stop "$SERVICE_NAME" 2>/dev/null || true
        systemctl disable "$SERVICE_NAME" 2>/dev/null || true
        rm -f "$SERVICE_FILE"
        systemctl daemon-reload
        crontab -l 2>/dev/null | grep -v "projectx-" | crontab -
        rm -f /usr/local/bin/projectx-backup.sh
        rm -f /usr/local/bin/projectx-healthcheck.sh
        rm -f /etc/logrotate.d/projectx
        success "ProjectX Bot удалён"
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
        echo "Без опций — полная установка и запуск"
        echo ""
        echo "Опции:"
        echo "  --uninstall    Полное удаление бота"
        echo "  --status       Показать статус"
        echo "  --logs         Показать логи в реальном времени"
        echo "  --restart      Перезапустить бота"
        echo "  --stop         Остановить бота"
        echo "  --start        Запустить бота"
        echo "  --backup       Создать бэкап БД вручную"
        echo "  --help         Показать эту справку"
        ;;
    *)
        main
        ;;
esac