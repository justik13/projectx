#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🗑️  ProjectX Bot — Safe Uninstaller (v3.0 Hardened)
# ═══════════════════════════════════════════════════════════════
set -euo pipefail
IFS=$'\n\t'

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

UNINSTALL_LOG="/var/log/projectx-uninstall.log"
TEMP_FILES=()

log() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$UNINSTALL_LOG"; }
error() { echo -e "${RED}[✗]${NC} $1" | tee -a "$UNINSTALL_LOG"; exit 1; }

cleanup() {
    for f in "${TEMP_FILES[@]}"; do rm -f "$f" 2>/dev/null; done
}
trap cleanup EXIT INT TERM

if [[ $EUID -ne 0 ]]; then error "Запустите с правами root (sudo)."; fi
mkdir -p /var/log
echo "=== Uninstall started: $(date) ===" > "$UNINSTALL_LOG"

log "Остановка сервиса..."
if systemctl is-active --quiet projectx-bot; then
    systemctl stop projectx-bot
    systemctl disable projectx-bot
    success "Сервис остановлен"
fi

log "Сканирование рабочей директории..."
PROJECT_DIR=$(systemctl show -p WorkingDirectory projectx-bot 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "[not set]" ]]; then
    PROJECT_DIR="/opt/projectx-bot"
fi

# 🔥 ЗАЩИТА ОТ SYMLINK-АТАКИ: Раскрываем реальный путь
if [[ -n "$PROJECT_DIR" ]]; then
    PROJECT_DIR=$(readlink -f "$PROJECT_DIR")
fi

if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "/opt" || "$PROJECT_DIR" == "/usr" || "$PROJECT_DIR" == "/root" || "$PROJECT_DIR" == "/home" || "$PROJECT_DIR" == "/etc" || "$PROJECT_DIR" == "/var" || "$PROJECT_DIR" == "/tmp" ]]; then
    error "Обнаружен небезопасный путь: '$PROJECT_DIR'. Прерывание."
fi

if [[ ! "$PROJECT_DIR" =~ projectx ]]; then
    error "Путь '$PROJECT_DIR' не содержит 'projectx'. Прерывание."
fi

success "Целевая директория: $PROJECT_DIR"

echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1) ${RED}Полное очищение${NC} (удалить ВСЁ)"
echo -e "2) ${GREEN}Удаление с сохранением данных${NC} (БД и .env в архив)"
echo -e "3) Отмена"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        read -p "⚠️ ${RED}ВНИМАНИЕ!${NC} Удалить ВСЁ безвозвратно? (yes/no): " confirm
        if [[ "$(echo "$confirm" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" != "yes" ]]; then
            success "Отменено"; exit 0
        fi

        log "Принудительный разрыв сессий PostgreSQL..."
        sudo -u postgres psql -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='projectx_bot' AND pid <> pg_backend_pid();" > /dev/null 2>&1 || true
        
        log "Удаление БД и пользователя..."
        sudo -u postgres psql -c "DROP DATABASE IF EXISTS projectx_bot;" > /dev/null 2>&1 || warn "Не удалось удалить БД"
        sudo -u postgres psql -c "DROP USER IF EXISTS projectx;" > /dev/null 2>&1 || warn "Не удалось удалить юзера"
        success "PostgreSQL очищен"

        log "Тотальное удаление файлов..."
        if [[ -d "$PROJECT_DIR" ]]; then rm -rf "$PROJECT_DIR"; success "Папка проекта удалена"; fi
        if [[ -d "/root/backups/projectx" ]]; then rm -rf "/root/backups/projectx"; success "Бэкапы удалены"; fi
        ;;
    2)
        log "Создание безопасного архива..."
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        SAFE_BACKUP_DIR="/root/projectx-backup-$TIMESTAMP"
        mkdir -p "$SAFE_BACKUP_DIR"

        sudo -u postgres pg_dump -Fc projectx_bot > "$SAFE_BACKUP_DIR/projectx_db.dump" 2>/dev/null && success "БД сохранена" || warn "Дамп БД не удался"
        if [[ -f "$PROJECT_DIR/.env" ]]; then cp "$PROJECT_DIR/.env" "$SAFE_BACKUP_DIR/"; success ".env сохранён"; fi
        
        rm -rf "$PROJECT_DIR"
        success "Папка очищена. Архив в: $SAFE_BACKUP_DIR"
        ;;
    3) success "Отменено"; exit 0 ;;
    *) error "Некорректный выбор" ;;
esac

log "Очистка системных конфигураций..."
rm -f /etc/nginx/sites-enabled/projectx /etc/nginx/sites-available/projectx
systemctl reload nginx 2>/dev/null || true

rm -f /etc/systemd/system/projectx-bot.service
systemctl daemon-reload

if crontab -l >/dev/null 2>&1; then
    CRONTAB_TMP=$(mktemp)
    TEMP_FILES+=("$CRONTAB_TMP")
    crontab -l | grep -v "projectx-" > "$CRONTAB_TMP" || true
    if [ -s "$CRONTAB_TMP" ]; then crontab "$CRONTAB_TMP"; else crontab -r || true; fi
fi

rm -f /usr/local/bin/projectx-backup.sh /usr/local/bin/projectx-healthcheck.sh
rm -f /var/log/projectx-*.log 2>/dev/null

if id "projectx" &>/dev/null; then
    # 🔥 ЗАЩИТА ОТ ЗАВИСШИХ ПРОЦЕССОВ: Убиваем всё перед удалением юзера
    pkill -u projectx 2>/dev/null || true
    sleep 1
    userdel projectx 2>/dev/null || true
    groupdel projectx 2>/dev/null || true
    success "Пользователь projectx удалён"
fi

echo ""
success "✨ Деинсталляция завершена!"
if [[ "$choice" == "2" ]]; then
    echo -e "${GREEN}📦 Архив данных: ${BLUE}$SAFE_BACKUP_DIR${NC}"
fi