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
   error "Скрипт должен быть запущен с правами root"
fi

# Остановка и отключение systemd сервиса projectx-bot
log "Остановка и отключение сервиса projectx-bot..."
if systemctl is-active --quiet projectx-bot; then
    systemctl stop projectx-bot
    systemctl disable projectx-bot
    success "Сервис projectx-bot остановлен и отключен"
else
    log "Сервис projectx-bot не запущен"
fi

# Определение папки проекта
log "Определение директории проекта..."
PROJECT_DIR=$(systemctl show -p WorkingDirectory projectx-bot 2>/dev/null | cut -d'=' -f2)
if [[ -z "$PROJECT_DIR" ]]; then
    PROJECT_DIR="$PWD"
    warn "Не удалось получить рабочую директорию из systemd, используется текущая: $PROJECT_DIR"
fi

success "Директория проекта: $PROJECT_DIR"

# Показ интерактивного меню
echo -e "\n========================================================"
echo -e "     🗑  ProjectX Bot - Удаление"
echo -e "========================================================"
echo -e "1) Полное удаление (удалить ВСЁ: код, БД, .env, бэкапы, пользователь)"
echo -e "2) Удаление с сохранением данных (сохранить БД, .env, бэкапы в архив)"
echo -e "3) Отмена (выйти без изменений)"
echo -e "========================================================"
read -p "Выберите вариант [1-3]: " choice

case $choice in
    1)
        echo ""
        read -p "Вы уверены, что хотите УДАЛИТЬ ВСЕ данные? (yes/no): " confirm
        if [[ "$confirm" != "yes" ]]; then
            success "Удаление отменено"
            exit 0
        fi

        # Полное удаление
        log "Выполняется полное удаление..."
        
        # Удаление папки проекта
        if [[ -d "$PROJECT_DIR" ]]; then
            rm -rf "$PROJECT_DIR"
            success "Директория проекта удалена: $PROJECT_DIR"
        else
            warn "Директория проекта не существует: $PROJECT_DIR"
        fi

        # Удаление бэкапов
        BACKUP_DIR="/root/backups/projectx"
        if [[ -d "$BACKUP_DIR" ]]; then
            rm -rf "$BACKUP_DIR"
            success "Директория бэкапов удалена: $BACKUP_DIR"
        else
            warn "Директория бэкапов не существует: $BACKUP_DIR"
        fi

        # Удаление пользователя projectx
        if id "projectx" &>/dev/null; then
            userdel -r projectx
            success "Пользователь projectx удален"
        else
            warn "Пользователь projectx не существует"
        fi
        ;;
    2)
        # Сохранение данных
        log "Выполняется сохранение данных..."
        
        # Создание директории бэкапа
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        BACKUP_DIR="/root/projectx-backup-$TIMESTAMP"
        mkdir -p "$BACKUP_DIR"
        success "Создана директория бэкапа: $BACKUP_DIR"

        # Копирование файлов базы данных
        DB_FILES=("$PROJECT_DIR/bot_data.db" "$PROJECT_DIR/bot_data.db-wal" "$PROJECT_DIR/bot_data.db-shm")
        for db_file in "${DB_FILES[@]}"; do
            if [[ -f "$db_file" ]]; then
                cp "$db_file" "$BACKUP_DIR/"
                success "Скопирован файл базы данных: $db_file"
            else
                warn "Файл базы данных не найден: $db_file"
            fi
        done

        # Копирование .env файла
        ENV_FILE="$PROJECT_DIR/.env"
        if [[ -f "$ENV_FILE" ]]; then
            cp "$ENV_FILE" "$BACKUP_DIR/"
            success "Скопирован файл конфигурации: $ENV_FILE"
        else
            warn "Файл конфигурации не найден: $ENV_FILE"
        fi

        # Копирование содержимого /root/backups/projectx/
        SOURCE_BACKUP="/root/backups/projectx"
        if [[ -d "$SOURCE_BACKUP" ]]; then
            cp -r "$SOURCE_BACKUP"/* "$BACKUP_DIR/"
            success "Скопированы бэкапы из $SOURCE_BACKUP"
        else
            warn "Директория бэкапов не существует: $SOURCE_BACKUP"
        fi

        success "Данные успешно сохранены в: $BACKUP_DIR"
        ;;
    3)
        success "Удаление отменено"
        exit 0
        ;;
    *)
        error "Неверный выбор. Выход."
        ;;
esac

# Общие действия для обоих вариантов
log "Выполняются общие действия по удалению..."

# Удаление systemd сервиса
SERVICE_FILE="/etc/systemd/system/projectx-bot.service"
if [[ -f "$SERVICE_FILE" ]]; then
    rm -f "$SERVICE_FILE"
    success "Удален файл сервиса: $SERVICE_FILE"
else
    warn "Файл сервиса не найден: $SERVICE_FILE"
fi

# Перезагрузка systemd
systemctl daemon-reload
success "Перезагружены systemd сервисы"

# Удаление cron задач
if crontab -l >/dev/null 2>&1; then
    crontab -l | grep -v "projectx-" > /tmp/crontab.tmp
    crontab /tmp/crontab.tmp
    rm -f /tmp/crontab.tmp
    success "Удалены cron задачи, связанные с projectx"
else
    warn "Crontab не существует или пуст"
fi

# Удаление скриптов
SCRIPTS=("/usr/local/bin/projectx-backup.sh" "/usr/local/bin/projectx-healthcheck.sh")
for script in "${SCRIPTS[@]}"; do
    if [[ -f "$script" ]]; then
        rm -f "$script"
        success "Удален скрипт: $script"
    else
        warn "Скрипт не найден: $script"
    fi
done

# Удаление logrotate конфига
LOGROTATE_FILE="/etc/logrotate.d/projectx"
if [[ -f "$LOGROTATE_FILE" ]]; then
    rm -f "$LOGROTATE_FILE"
    success "Удален logrotate конфиг: $LOGROTATE_FILE"
else
    warn "Logrotate конфиг не найден: $LOGROTATE_FILE"
fi

# Удаление лог файлов
rm -f /var/log/projectx-*.log 2>/dev/null
success "Лог файлы удалены (если существовали)"

success "Процесс удаления завершен успешно!"
echo ""
echo "Итоговый отчет:"
echo "- Сервис projectx-bot остановлен и отключен"
echo "- Файл сервиса удален: $SERVICE_FILE"
echo "- Systemd перезагружен"
echo "- Cron задачи очищены"
echo "- Скрипты удалены: ${SCRIPTS[*]}"
echo "- Logrotate конфиг удален: $LOGROTATE_FILE"
echo "- Лог файлы удалены"
if [[ "$choice" == "2" ]]; then
    echo "- Данные сохранены в: $BACKUP_DIR"
fi
