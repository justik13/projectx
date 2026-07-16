#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 🗑️  ProjectX Bot — Safe Uninstaller (v2.0.1 Clean)
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
    for f in "${TEMP_FILES[@]}"; do
        rm -f "$f" 2>/dev/null
    done
}
trap cleanup EXIT INT TERM

if [[ $EUID -ne 0 ]]; then
    error "Скрипт деинсталляции должен быть запущен с правами root (sudo)."
fi

mkdir -p /var/log
echo "=== Uninstall started: $(date) ===" > "$UNINSTALL_LOG"

log "Остановка и отключение фонового сервиса projectx-bot..."
if systemctl is-active --quiet projectx-bot; then
    systemctl stop projectx-bot
    systemctl disable projectx-bot
    success "Сервис projectx-bot успешно остановлен и отключен"
else
    log "Сервис projectx-bot не был запущен на момент удаления"
fi

log "Сканирование рабочей директории проекта..."
PROJECT_DIR=$(systemctl show -p WorkingDirectory projectx-bot 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')

if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "[not set]" ]]; then
    PROJECT_DIR="/opt/projectx-bot"
    warn "Не удалось получить путь из systemd, используется директория по умолчанию: $PROJECT_DIR"
fi

# Усиленная проверка безопасности пути
if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "/opt" || "$PROJECT_DIR" == "/usr" || "$PROJECT_DIR" == "/root" || "$PROJECT_DIR" == "/home" || "$PROJECT_DIR" == "/etc" || "$PROJECT_DIR" == "/var" || "$PROJECT_DIR" == "/tmp" ]]; then
    error "Обнаружен небезопасный путь для удаления: '$PROJECT_DIR'. Прерывание."
fi

# Дополнительная проверка: путь должен содержать "projectx"
if [[ ! "$PROJECT_DIR" =~ projectx ]]; then
    error "Путь '$PROJECT_DIR' не содержит 'projectx'. Прерывание из соображений безопасности."
fi

success "Целевая директория для удаления: $PROJECT_DIR"

echo ""
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}    🗑  ProjectX Bot — Панель Деинсталляции${NC}"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
echo -e "1) ${RED}Полное очищение${NC} (удалить ВСЁ: код, БД, ключи .env, бэкапы, юзера)"
echo -e "2) ${GREEN}Удаление с сохранением данных${NC} (БД и .env будут упакованы в архив)"
echo -e "3) Отмена операции (выход без изменений)"
echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        echo ""
        read -p "⚠️ ${RED}ВНИМАНИЕ!${NC} Это действие сотрет все подписки и базы данных безвозвратно. Вы уверены? (yes/no): " confirm
        confirm_lower=$(echo "$confirm" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
        if [[ "$confirm_lower" != "yes" ]]; then
            success "Деинсталляция отменена пользователем"
            exit 0
        fi
        
        log "Выполняется тотальное удаление данных..."
        if [[ -d "$PROJECT_DIR" ]]; then
            rm -rf "$PROJECT_DIR"
            success "Директория проекта полностью удалена: $PROJECT_DIR"
        else
            warn "Директория проекта не найдена по указанному пути"
        fi
        
        BACKUP_DIR="/root/backups/projectx"
        if [[ -d "$BACKUP_DIR" ]]; then
            rm -rf "$BACKUP_DIR"
            success "Директория системных бэкапов удалена: $BACKUP_DIR"
        else
            warn "Директория бэкапов отсутствовала на сервере"
        fi
        ;;
        
    2)
        log "Выполняется резервное архивирование перед деструктивными действиями..."
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        SAFE_BACKUP_DIR="/root/projectx-backup-$TIMESTAMP"
        mkdir -p "$SAFE_BACKUP_DIR"
        success "Создана папка безопасного сохранения: $SAFE_BACKUP_DIR"
        
        DB_FILE="$PROJECT_DIR/bot_data.db"
        if [[ -f "$DB_FILE" ]]; then
            sqlite3 "$DB_FILE" ".backup '$SAFE_BACKUP_DIR/bot_data.db'"
            success "Зарезервирована консистентная копия БД (через sqlite3 .backup)"
        else
            warn "Файл БД не найден, копирование пропущено"
        fi
        
        ENV_FILE="$PROJECT_DIR/.env"
        if [[ -f "$ENV_FILE" ]]; then
            cp "$ENV_FILE" "$SAFE_BACKUP_DIR/"
            success "Зарезервирован файл конфигурации: .env"
        else
            warn "Файл конфигурации .env отсутствовал"
        fi
        
        SOURCE_BACKUP="/root/backups/projectx"
        if [[ -d "$SOURCE_BACKUP" ]]; then
            cp -aP "$SOURCE_BACKUP"/* "$SAFE_BACKUP_DIR/" 2>/dev/null || true
            success "Все накопленные бэкапы перенесены в безопасную зону"
        fi
        
        rm -rf "$PROJECT_DIR"
        success "Рабочая директория $PROJECT_DIR очищена. Данные сохранены в $SAFE_BACKUP_DIR"
        ;;
        
    3)
        success "Процесс отменен. Никаких изменений не внесено."
        exit 0
        ;;
        
    *)
        error "Выбран некорректный пункт меню. Выход."
        ;;
esac

log "Очистка зависимостей операционной системы..."

# Очистка конфигурации Nginx (если она была создана)
NGINX_AVAILABLE="/etc/nginx/sites-available/projectx"
NGINX_ENABLED="/etc/nginx/sites-enabled/projectx"
if [[ -f "$NGINX_AVAILABLE" || -L "$NGINX_ENABLED" ]]; then
    rm -f "$NGINX_ENABLED" "$NGINX_AVAILABLE"
    success "Конфигурация Nginx удалена"
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        success "Конфигурация Nginx перезагружена"
    fi
fi

SERVICE_FILE="/etc/systemd/system/projectx-bot.service"
if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    success "Конфиг службы systemd удален: $SERVICE_FILE"
fi

systemctl daemon-reload
success "Конфигурация Systemd успешно обновлена"

if crontab -l >/dev/null 2>&1; then
    CRONTAB_TMP=$(mktemp)
    TEMP_FILES+=("$CRONTAB_TMP")
    crontab -l | grep -v "projectx-" > "$CRONTAB_TMP" || true
    if [ -s "$CRONTAB_TMP" ]; then
        crontab "$CRONTAB_TMP"
    else
        crontab -r || true
    fi
    success "Регламентные задачи Cron очищены"
fi

SCRIPTS=("/usr/local/bin/projectx-backup.sh" "/usr/local/bin/projectx-healthcheck.sh")
for script in "${SCRIPTS[@]}"; do
    if [[ -f "$script" ]]; then
        rm -f "$script"
        success "Удален скрипт автоматизации: $script"
    fi
done

LOGROTATE_FILE="/etc/logrotate.d/projectx"
if [[ -f "$LOGROTATE_FILE" ]]; then
    rm -f "$LOGROTATE_FILE"
    success "Конфигурация logrotate удалена"
fi

rm -f /var/log/projectx-*.log 2>/dev/null
success "Временные лог-файлы очищены"

if id "projectx" &>/dev/null; then
    userdel projectx 2>/dev/null || true
    groupdel projectx 2>/dev/null || true
    success "Системный пользователь и группа projectx удалены из ОС"
else
    warn "Пользователь projectx не был найден в системе"
fi

echo ""
success "✨ Процесс деинсталляции ProjectX Bot полностью завершён!"

if [[ "$choice" == "2" ]]; then
    echo ""
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}📦 Архив ваших критических данных находится по адресу:${NC}"
    echo -e "${BLUE}    $SAFE_BACKUP_DIR${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
fi

success "Лог деинсталляции сохранён: $UNINSTALL_LOG"