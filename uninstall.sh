#!/bin/bash

# Цвета
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции логирования
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

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
PROJECT_DIR=$(systemctl show -p WorkingDirectory projectx-bot 2>/dev/null | cut -d'=' -f2)
if [[ -z "$PROJECT_DIR" || "$PROJECT_DIR" == "[not set]" ]]; then
    PROJECT_DIR="/opt/projectx-bot"
    warn "Не удалось получить путь из systemd, используется директория по умолчанию: $PROJECT_DIR"
fi

success "Целевая директория для удаления: $PROJECT_DIR"

# Показ интерактивного меню
echo -e "\n========================================================"
echo -e "     🗑  ProjectX Bot — Панель Деинсталляции"
echo -e "========================================================"
echo -e "1) Полное очищение (удалить ВСЁ: код, БД, ключи .env, бэкапы, юзера)"
echo -e "2) Удаление с сохранением данных (БД, .env и бэкапы будут упакованы в архив)"
echo -e "3) Отмена операции (выход без изменений)"
echo -e "========================================================"
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        echo ""
        read -p "⚠️ ВНИМАНИЕ! Это действие сотрет все подписки и базы данных безвозвратно. Вы уверены? (yes/no): " confirm
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

        # Удаление пользователя projectx
        if id "projectx" &>/dev/null; then
            userdel -r projectx 2>/dev/null || userdel projectx
            success "Системный пользователь projectx удален из ОС"
        else
            warn "Пользователь projectx не был найден в системе"
        fi
        ;;
    2)
        log "Выполняется резервное архивирование перед деструктивными действиями..."
        
        # Создание директории бэкапа
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        SAFE_BACKUP_DIR="/root/projectx-backup-$TIMESTAMP"
        mkdir -p "$SAFE_BACKUP_DIR"
        success "Создана папка безопасного сохранения: $SAFE_BACKUP_DIR"

        # Копирование файлов базы данных (включая WAL журналы для сохранения консистентности)
        DB_FILES=("$PROJECT_DIR/bot_data.db" "$PROJECT_DIR/bot_data.db-wal" "$PROJECT_DIR/bot_data.db-shm")
        for db_file in "${DB_FILES[@]}"; do
            if [[ -f "$db_file" ]]; then
                cp "$db_file" "$SAFE_BACKUP_DIR/"
                success "Зарезервирован компонент БД: $(basename "$db_file")"
            fi
        done

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

# Общие действия по очистке системных триггеров
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
    crontab -l | grep -v "projectx-" > /tmp/crontab.tmp || true
    if [ -s /tmp/crontab.tmp ]; then
        crontab /tmp/crontab.tmp
    else
        crontab -r || true
    fi
    rm -f /tmp/crontab.tmp
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

echo ""
success "✨ Процесс деинсталляции ProjectX Bot полностью завершён!"
if [[ "$choice" == "2" ]]; then
    echo -e "${YELLOW}Архив ваших критических данных находится по адресу: ${SAFE_BACKUP_DIR}${NC}"
fi