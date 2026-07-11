#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# ProjectX Bot — Uninstall Script
# Безопасное удаление бота с опцией сохранения данных
# ═══════════════════════════════════════════════════════════════

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции логирования
log() { echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# 🔥 FIX P0: Trap для очистки временных файлов при прерывании (Ctrl+C)
TEMP_FILES=()
cleanup() {
    for f in "${TEMP_FILES[@]}"; do
        rm -f "$f" 2>/dev/null
    done
}
trap cleanup EXIT INT TERM

# Проверка запуска от root
if [[ $EUID -ne 0 ]]; then
    error "Скрипт деинсталляции должен быть запущен с правами root (sudo)."
fi

# Остановка и отключение systemd сервиса projectx-bot
log "Остановка и отключение фонового сервиса projectx-bot..."
if systemctl is-active --quiet projectx-bot; then
    systemctl stop projectx-bot
    systemctl disable projectx-bot
    success "Сервис projectx-bot успешно остановлен и отключен"
else
    log "Сервис projectx-bot не был запущен на момент удаления"
fi

# Определение папки проекта из конфигурации systemd
log "Сканирование рабочей директории проекта..."
# 🔥 FIX P0: Очистка от пробелов и переносов строк
PROJECT_DIR=$(systemctl show -p WorkingDirectory projectx-bot 2>/dev/null | cut -d'=' -f2 | tr -d '[:space:]')
if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "[not set]" ]]; then
    PROJECT_DIR="/opt/projectx-bot"
    warn "Не удалось получить путь из systemd, используется директория по умолчанию: $PROJECT_DIR"
fi

# 🔥 FIX P0: Жесткая защита от случайного удаления системных директорий
if [[ "$PROJECT_DIR" == "/" || "$PROJECT_DIR" == "/opt" || "$PROJECT_DIR" == "/usr" || "$PROJECT_DIR" == "/root" || "$PROJECT_DIR" == "/home" || "$PROJECT_DIR" == "/etc" ]]; then
    error "Обнаружен небезопасный путь для удаления: '$PROJECT_DIR'. Прерывание."
fi

success "Целевая директория для удаления: $PROJECT_DIR"

# Показ интерактивного меню
echo ""
echo -e "${YELLOW}========================================================${NC}"
echo -e "${YELLOW}     🗑  ProjectX Bot — Панель Деинсталляции${NC}"
echo -e "${YELLOW}========================================================${NC}"
echo -e "1) ${RED}Полное очищение${NC} (удалить ВСЁ: код, БД, ключи .env, бэкапы, юзера)"
echo -e "2) ${GREEN}Удаление с сохранением данных${NC} (БД и .env будут упакованы в архив)"
echo -e "3) Отмена операции (выход без изменений)"
echo -e "${YELLOW}========================================================${NC}"
read -p "Выберите вариант [1-3]: " choice

case $choice in
1)
    echo ""
    read -p "⚠️ ${RED}ВНИМАНИЕ!${NC} Это действие сотрет все подписки и базы данных безвозвратно. Вы уверены? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        success "Деинсталляция отменена пользователем"
        exit 0
    fi
    log "Выполняется тотальное удаление данных..."
    
    # Удаление папки проекта
    if [[ -d "$PROJECT_DIR" ]]; then
        rm -rf "$PROJECT_DIR"
        success "Директория проекта полностью удалена: $PROJECT_DIR"
    else
        warn "Директория проекта не найдена по указанному пути"
    fi
    
    # Удаление бэкапов
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
    
    # Создание директории бэкапа
    TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
    SAFE_BACKUP_DIR="/root/projectx-backup-$TIMESTAMP"
    mkdir -p "$SAFE_BACKUP_DIR"
    success "Создана папка безопасного сохранения: $SAFE_BACKUP_DIR"
    
    # 🔥 FIX P0: Используем sqlite3 .backup для гарантированной целостности БД
    DB_FILE="$PROJECT_DIR/bot_data.db"
    if [[ -f "$DB_FILE" ]]; then
        sqlite3 "$DB_FILE" ".backup '$SAFE_BACKUP_DIR/bot_data.db'"
        success "Зарезервирована консистентная копия БД (через sqlite3 .backup)"
    else
        warn "Файл БД не найден, копирование пропущено"
    fi
    
    # Копирование .env файла
    ENV_FILE="$PROJECT_DIR/.env"
    if [[ -f "$ENV_FILE" ]]; then
        cp "$ENV_FILE" "$SAFE_BACKUP_DIR/"
        success "Зарезервирован файл конфигурации: .env"
    else
        warn "Файл конфигурации .env отсутствовал"
    fi
    
    # Перенос старых регламентных бэкапов
    SOURCE_BACKUP="/root/backups/projectx"
    if [[ -d "$SOURCE_BACKUP" ]]; then
        cp -r "$SOURCE_BACKUP"/* "$SAFE_BACKUP_DIR/" 2>/dev/null || true
        success "Все накопленные бэкапы перенесены в безопасную зону"
    fi
    
    # Теперь, когда данные спасены, безопасно удаляем рабочую директорию
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

# ═══════════════════════════════════════════════════════════════
# Общие действия по очистке системных триггеров
# ═══════════════════════════════════════════════════════════════

log "Очистка зависимостей операционной системы..."

# Удаление systemd сервиса
SERVICE_FILE="/etc/systemd/system/projectx-bot.service"
if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    success "Конфиг службы systemd удален: $SERVICE_FILE"
fi

# Перезагрузка системных демонов
systemctl daemon-reload
success "Конфигурация Systemd успешно обновлена"

# Удаление cron задач мониторинга и резервирования
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

# Удаление исполняемых скриптов автоматизации
SCRIPTS=("/usr/local/bin/projectx-backup.sh" "/usr/local/bin/projectx-healthcheck.sh")
for script in "${SCRIPTS[@]}"; do
    if [[ -f "$script" ]]; then
        rm -f "$script"
        success "Удален скрипт автоматизации: $script"
    fi
done

# Удаление logrotate конфига
LOGROTATE_FILE="/etc/logrotate.d/projectx"
if [[ -f "$LOGROTATE_FILE" ]]; then
    rm -f "$LOGROTATE_FILE"
    success "Конфигурация logrotate удалена"
fi

# Полное удаление лог-файлов
rm -f /var/log/projectx-*.log 2>/dev/null
success "Временные лог-файлы очищены"

# 🔥 FIX P0: Удаление пользователя И группы projectx
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
    echo -e "${BLUE}   $SAFE_BACKUP_DIR${NC}"
    echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
fi