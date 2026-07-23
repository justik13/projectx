#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# setup-public.sh — Настройка публичного HTTPS доступа к Amnezia API
# ═══════════════════════════════════════════════════════════════
# Использование:
#   Интерактивно:   bash ./scripts/setup-public.sh
#   С аргументами:  bash ./scripts/setup-public.sh --domain api.example.com --email admin@example.com
#
# Требования:
#   - Ubuntu/Debian
#   - Amnezia API уже установлен и слушает localhost:4001
#   - Домен с A-записью, указывающей на этот сервер
#   - Порты 80 и 8443 доступны извне (не заблокированы провайдером)
#
# Результат:
#   - API доступен по https://ваш-домен.com:8443
#   - API-ключ передаётся по HTTPS (зашифрованно)
#   - Amnezia API остаётся на 127.0.0.1:4001 (безопасно)
#   - /docs и /metrics не светятся публично
# ═══════════════════════════════════════════════════════════════

set -euo pipefail
IFS=$'\n\t'

# ─────────────────────────────────────────────────────────────
# Цвета и логирование
# ─────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

LOG_FILE="/var/log/amnezia-api-public-setup.log"
NGINX_CONF="/etc/nginx/sites-available/amnezia-api"
NGINX_LINK="/etc/nginx/sites-enabled/amnezia-api"
AMNEZIA_PORT=4001
AMNEZIA_HOST="127.0.0.1"

# Переменные (могут быть установлены через аргументы)
DOMAIN=""
EMAIL=""

log()     { echo -e "${BLUE}[$(date +'%H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[✗]${NC} $1" | tee -a "$LOG_FILE"; exit 1; }
info()    { echo -e "${CYAN}[i]${NC} $1" | tee -a "$LOG_FILE"; }
header()  {
    echo -e "\n${BOLD}${BLUE}══════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}${BLUE}  $1${NC}"
    echo -e "${BOLD}${BLUE}══════════════════════════════════════════════════════════${NC}\n"
}

# ─────────────────────────────────────────────────────────────
# Парсинг аргументов
# ─────────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --domain)
                DOMAIN="$2"
                shift 2
                ;;
            --email)
                EMAIL="$2"
                shift 2
                ;;
            -h|--help)
                echo "Использование: $0 [--domain DOMAIN] [--email EMAIL]"
                echo ""
                echo "Опции:"
                echo "  --domain DOMAIN   Домен для HTTPS (например: api.example.com)"
                echo "  --email EMAIL     Email для Let's Encrypt"
                echo "  -h, --help        Показать эту справку"
                exit 0
                ;;
            *)
                error "Неизвестный аргумент: $1. Используйте --help"
                ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────
preflight_checks() {
    header "🔍 Pre-flight проверки"

    # Root
    if [[ $EUID -ne 0 ]]; then
        error "Скрипт должен запускаться от имени root (sudo bash $0)"
    fi
    success "Запущен от имени root"

    # Ubuntu/Debian
    if [[ ! -f /etc/os-release ]]; then
        error "Не удалось определить операционную систему"
    fi
    source /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        error "Поддерживаются только Ubuntu и Debian. Обнаружено: $ID"
    fi
    success "ОС: $PRETTY_NAME"

    # Amnezia API слушает localhost:4001
    if ! ss -tlnp 2>/dev/null | grep -qE ":${AMNEZIA_PORT}\s"; then
        warn "Amnezia API не слушает порт ${AMNEZIA_PORT}"
        echo ""
        echo -e "${YELLOW}Возможные причины:${NC}"
        echo "  • Amnezia API не установлен — запустите сначала: bash ./scripts/setup.sh"
        echo "  • API слушает на другом порту — измените AMNEZIA_PORT в начале этого скрипта"
        echo ""
        read -p "Продолжить настройку всё равно? (y/N): " -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            error "Настройка отменена"
        fi
    else
        # Проверяем что слушает именно localhost
        if ss -tlnp 2>/dev/null | grep -qE "${AMNEZIA_HOST}:${AMNEZIA_PORT}\s"; then
            success "Amnezia API слушает ${AMNEZIA_HOST}:${AMNEZIA_PORT} (безопасно)"
        else
            warn "Amnezia API слушает НЕ на localhost — это небезопасно!"
            echo -e "${YELLOW}Рекомендуется оставить FASTIFY_ROUTES=${AMNEZIA_HOST}:${AMNEZIA_PORT} в .env${NC}"
        fi
    fi

    # Проверка healthcheck
    if command -v curl &>/dev/null; then
        if curl -s -f -o /dev/null "http://${AMNEZIA_HOST}:${AMNEZIA_PORT}/healthz"; then
            success "Amnezia API отвечает на /healthz"
        else
            warn "Amnezia API не отвечает на /healthz (может быть нормально если API ещё не запущен)"
        fi
    fi
}

# ─────────────────────────────────────────────────────────────
# Сбор домена и email
# ─────────────────────────────────────────────────────────────
collect_domain_and_email() {
    header "🌐 Настройка домена"

    # Получаем публичный IP сервера
    local PUBLIC_IP=""
    for service in "https://api.ipify.org" "https://ifconfig.me" "https://icanhazip.com"; do
        if PUBLIC_IP=$(curl -s -f --max-time 5 "$service" 2>/dev/null); then
            break
        fi
    done

    if [[ -z "$PUBLIC_IP" ]]; then
        warn "Не удалось автоматически определить публичный IP сервера"
    else
        info "Публичный IP этого сервера: ${BOLD}${PUBLIC_IP}${NC}"
    fi

    # Домен
    if [[ -z "$DOMAIN" ]]; then
        echo ""
        echo -e "${CYAN}Введите домен, который будет указывать на этот сервер.${NC}"
        echo -e "${CYAN}Домен должен иметь A-запись на IP сервера (настраивается у регистратора).${NC}"
        echo ""
        echo -e "Примеры: ${BOLD}api.example.com${NC}, ${BOLD}vpn.mydomain.ru${NC}"
        if [[ -n "$PUBLIC_IP" ]]; then
            echo -e "\nСоздайте A-запись: ${BOLD}ваш-домен → ${PUBLIC_IP}${NC}"
        fi
        echo ""
        while true; do
            read -p "Домен (например api.example.com): " -r DOMAIN
            if [[ -z "$DOMAIN" ]]; then
                warn "Домен не может быть пустым"
                continue
            fi
            if [[ ! "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
                warn "Некорректный формат домена"
                continue
            fi
            break
        done
    fi

    # Проверка DNS
    info "Проверяю DNS для ${BOLD}${DOMAIN}${NC}..."
    local RESOLVED_IP=""
    if command -v dig &>/dev/null; then
        RESOLVED_IP=$(dig +short "$DOMAIN" A 2>/dev/null | head -n1)
    elif command -v host &>/dev/null; then
        RESOLVED_IP=$(host "$DOMAIN" 2>/dev/null | grep "has address" | head -n1 | awk '{print $NF}')
    elif command -v getent &>/dev/null; then
        RESOLVED_IP=$(getent hosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -n1)
    fi

    if [[ -z "$RESOLVED_IP" ]]; then
        warn "Не удалось определить IP для домена ${DOMAIN}"
        echo -e "${YELLOW}Возможно:${NC}"
        echo "  • DNS ещё не распространён (подождите 5-60 минут)"
        echo "  • A-запись не создана у регистратора"
        echo "  • Установите dnsutils: apt install dnsutils"
        echo ""
        read -p "Продолжить без проверки DNS? (y/N): " -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            error "Настройка отменена"
        fi
    elif [[ -n "$PUBLIC_IP" && "$RESOLVED_IP" != "$PUBLIC_IP" ]]; then
        warn "Домен ${DOMAIN} указывает на ${RESOLVED_IP}, но публичный IP сервера: ${PUBLIC_IP}"
        echo -e "${YELLOW}Certbot может не пройти проверку!${NC}"
        read -p "Продолжить? (y/N): " -r
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            error "Настройка отменена"
        fi
    else
        success "DNS: ${DOMAIN} → ${RESOLVED_IP}"
    fi

    # Email
    if [[ -z "$EMAIL" ]]; then
        echo ""
        echo -e "${CYAN}Email для уведомлений Let's Encrypt о продлении сертификата.${NC}"
        while true; do
            read -p "Email: " -r EMAIL
            if [[ -z "$EMAIL" ]]; then
                warn "Email не может быть пустым"
                continue
            fi
            if [[ ! "$EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
                warn "Некорректный формат email"
                continue
            fi
            break
        done
    fi

    success "Домен: ${BOLD}${DOMAIN}${NC}"
    success "Email: ${BOLD}${EMAIL}${NC}"
}

# ─────────────────────────────────────────────────────────────
# Установка Nginx + Certbot
# ─────────────────────────────────────────────────────────────
install_dependencies() {
    header "📦 Установка Nginx и Certbot"

    log "Обновление списка пакетов..."
    apt-get update -qq || error "Не удалось обновить apt"

    local packages=(nginx certbot python3-certbot-nginx)
    local to_install=()

    for pkg in "${packages[@]}"; do
        if ! dpkg -l | grep -q "^ii  $pkg "; then
            to_install+=("$pkg")
        fi
    done

    if [[ ${#to_install[@]} -eq 0 ]]; then
        success "Все зависимости уже установлены"
        return
    fi

    log "Устанавливаю: ${to_install[*]}"
    local install_log=$(mktemp)
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y "${to_install[@]}" > "$install_log" 2>&1; then
        error "Ошибка установки. Лог: $install_log\n$(tail -20 "$install_log")"
    fi
    rm -f "$install_log"

    success "Зависимости установлены"
}

# ─────────────────────────────────────────────────────────────
# Firewall
# ─────────────────────────────────────────────────────────────
setup_firewall() {
    if ! command -v ufw &>/dev/null; then
        info "UFW не установлен — пропуск настройки firewall"
        return
    fi

    if ! ufw status | grep -q "Status: active"; then
        info "UFW не активен — пропуск настройки firewall"
        return
    fi

    header "🔥 Настройка UFW"

    ufw allow 80/tcp comment 'HTTP (Let'\''s Encrypt challenge)' >/dev/null 2>&1 || true
    # ═══ ИЗМЕНЕНО: порт 8443 вместо 443 ═══
    ufw allow 8443/tcp comment 'HTTPS (Amnezia API)' >/dev/null 2>&1 || true

    success "UFW: порты 80 и 8443 открыты"
}

# ─────────────────────────────────────────────────────────────
# Базовый HTTP конфиг Nginx (для certbot challenge)
# ─────────────────────────────────────────────────────────────
create_initial_nginx_config() {
    header "⚙️ Настройка Nginx (шаг 1/2: HTTP)"

    # Удаляем default если мешает
    if [[ -L /etc/nginx/sites-enabled/default ]]; then
        if grep -q "listen 80 default_server" /etc/nginx/sites-available/default 2>/dev/null; then
            log "Отключаю default сайт (мешает нашему домену)"
            rm -f /etc/nginx/sites-enabled/default
        fi
    fi

    cat > "$NGINX_CONF" <<NGINX_EOF
# Amnezia API — публичный HTTPS reverse proxy
# Сгенерировано setup-public.sh $(date '+%Y-%m-%d %H:%M:%S')

# Rate limiting zone (защита от брутфорса)
limit_req_zone \$binary_remote_addr zone=amnezia_api_limit:10m rate=10r/s;

server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Let's Encrypt challenge (обязательно для certbot)
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    # Временно все остальные запросы возвращают 404
    # После получения SSL certbot сам настроит redirect
    location / {
        return 404;
    }
}
NGINX_EOF

    # Создаём директорию для certbot challenge
    mkdir -p /var/www/certbot

    ln -sf "$NGINX_CONF" "$NGINX_LINK"

    if ! nginx -t >/dev/null 2>&1; then
        error "Nginx конфиг невалиден. Проверьте: nginx -t"
    fi

    systemctl reload nginx || error "Не удалось перезапустить Nginx"

    success "Nginx: HTTP конфиг активен для ${DOMAIN}"
}

# ─────────────────────────────────────────────────────────────
# Получение SSL сертификата
# ─────────────────────────────────────────────────────────────
obtain_ssl_certificate() {
    header "🔒 Получение SSL сертификата"

    info "Запускаю certbot (это может занять до 2 минут)..."
    info "Certbot проверит что домен ${DOMAIN} указывает на этот сервер"

    local certbot_log=$(mktemp)

    if ! certbot certonly \
        --webroot \
        -w /var/www/certbot \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        --no-eff-email \
        --keep-until-expiring \
        > "$certbot_log" 2>&1; then

        echo -e "${RED}══════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  Certbot не смог получить сертификат${NC}"
        echo -e "${RED}══════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "${YELLOW}Возможные причины:${NC}"
        echo "  1. DNS A-запись для ${DOMAIN} не указывает на этот сервер"
        echo "  2. Порт 80 недоступен извне (firewall провайдера, cloud firewall)"
        echo "  3. Домен ещё не распространился в DNS (подождите 5-60 минут)"
        echo "  4. Лимит Let's Encrypt исчерпан (5 сертификатов/неделю на домен)"
        echo ""
        echo -e "${YELLOW}Диагностика:${NC}"
        tail -30 "$certbot_log"
        echo ""
        echo -e "${YELLOW}Лог: ${certbot_log}${NC}"
        exit 1
    fi

    rm -f "$certbot_log"
    success "SSL сертификат получен!"
}

# ─────────────────────────────────────────────────────────────
# Финальный HTTPS конфиг с reverse proxy
# ═══ ИЗМЕНЕНО: порт 8443 вместо 443 ═══
# ─────────────────────────────────────────────────────────────
create_final_nginx_config() {
    header "⚙️ Настройка Nginx (шаг 2/2: HTTPS + reverse proxy)"

    local CERT_PATH="/etc/letsencrypt/live/${DOMAIN}"

    cat > "$NGINX_CONF" <<NGINX_EOF
# ═══════════════════════════════════════════════════════════════
# Amnezia API — HTTPS reverse proxy с rate limiting
# ═══════════════════════════════════════════════════════════════
# Сгенерировано: $(date '+%Y-%m-%d %H:%M:%S')
# Домен: ${DOMAIN}
# Backend: ${AMNEZIA_HOST}:${AMNEZIA_PORT}
# ═══════════════════════════════════════════════════════════════

# Rate limiting zone (10 запросов/сек на IP, burst 20)
limit_req_zone \$binary_remote_addr zone=amnezia_api_limit:10m rate=10r/s;

# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Разрешаем certbot renewal
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        allow all;
    }

    # Всё остальное → HTTPS на порт 8443
    location / {
        return 301 https://\$host:8443\$request_uri;
    }
}

# HTTPS server
server {
    listen 8443 ssl http2;
    listen [::]:8443 ssl http2;
    server_name ${DOMAIN};

    # SSL сертификаты (Let's Encrypt)
    ssl_certificate ${CERT_PATH}/fullchain.pem;
    ssl_certificate_key ${CERT_PATH}/privkey.pem;
    ssl_trusted_certificate ${CERT_PATH}/chain.pem;

    # Современные SSL настройки (mozilla recommended)
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;
    ssl_session_tickets off;

    # OCSP stapling
    ssl_stapling on;
    ssl_stapling_verify on;
    resolver 1.1.1.1 8.8.8.8 valid=300s;
    resolver_timeout 5s;

    # Безопасные заголовки
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Логирование
    access_log /var/log/nginx/amnezia-api-access.log;
    error_log /var/log/nginx/amnezia-api-error.log;

    # Размер тела запроса (для бэкапов)
    client_max_body_size 50M;

    # Таймауты (для долгих операций API)
    proxy_connect_timeout 30s;
    proxy_send_timeout 60s;
    proxy_read_timeout 120s;

    # ═══════════════════════════════════════════════════════════
    # Запрет публичного доступа к документации и метрикам
    # ═══════════════════════════════════════════════════════════
    location ~* ^/(docs|documentation|metrics|static)(/|\$) {
        return 404;
    }

    # ═══════════════════════════════════════════════════════════
    # Reverse proxy на локальный Amnezia API
    # ═══════════════════════════════════════════════════════════
    location / {
        # Rate limiting
        limit_req zone=amnezia_api_limit burst=20 nodelay;
        limit_req_status 429;

        # Проксирование на локальный API
        proxy_pass http://${AMNEZIA_HOST}:${AMNEZIA_PORT};
        proxy_http_version 1.1;

        # Пробрасываем заголовки
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;

        # WebSocket support (на будущее)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Отключаем буферизацию для streaming ответов
        proxy_buffering off;
    }
}
NGINX_EOF

    if ! nginx -t >/dev/null 2>&1; then
        error "Nginx конфиг невалиден. Проверьте: nginx -t"
    fi

    systemctl reload nginx || error "Не удалось перезапустить Nginx"

    success "Nginx: HTTPS reverse proxy активен"
}

# ─────────────────────────────────────────────────────────────
# Автопродление сертификата
# ─────────────────────────────────────────────────────────────
setup_auto_renewal() {
    header "🔄 Автопродление сертификата"

    # Certbot на Ubuntu/Debian сам создаёт systemd timer или cron
    if systemctl list-timers --all 2>/dev/null | grep -q "certbot.timer"; then
        success "Certbot timer уже активен (автопродление настроено)"
        return
    fi

    if [[ -f /etc/cron.d/certbot ]]; then
        success "Certbot cron уже настроен (автопродление настроено)"
        return
    fi

    # Создаём cron вручную
    log "Создаю cron задачу для автопродления..."
    cat > /etc/cron.d/certbot-amnezia <<EOF
# Certbot auto-renewal for Amnezia API
# Запускается 2 раза в день, продлевает если до истечения < 30 дней
0 */12 * * * root certbot renew --quiet --deploy-hook "systemctl reload nginx"
EOF
    chmod 644 /etc/cron.d/certbot-amnezia

    success "Cron для автопродления настроен (проверка каждые 12 часов)"
}

# ─────────────────────────────────────────────────────────────
# Финальная проверка
# ─────────────────────────────────────────────────────────────
final_verification() {
    header "✅ Финальная проверка"

    # Проверка что Nginx слушает 8443
    if ss -tlnp 2>/dev/null | grep -qE ":8443\s"; then
        success "Nginx слушает порт 8443 (HTTPS)"
    else
        warn "Nginx не слушает порт 8443"
    fi

    # Проверка что Amnezia API всё ещё на localhost
    if ss -tlnp 2>/dev/null | grep -qE "${AMNEZIA_HOST}:${AMNEZIA_PORT}\s"; then
        success "Amnezia API слушает ${AMNEZIA_HOST}:${AMNEZIA_PORT} (не торчит в интернет)"
    fi

    # Healthcheck через HTTPS
    info "Проверяю healthcheck через HTTPS..."
    if command -v curl &>/dev/null; then
        local health_status
        health_status=$(curl -s -o /dev/null -w "%{http_code}" "https://${DOMAIN}:8443/healthz" --max-time 10 2>/dev/null || echo "000")

        if [[ "$health_status" == "200" ]]; then
            success "HTTPS healthcheck: ${BOLD}200 OK${NC}"
        else
            warn "HTTPS healthcheck вернул статус: ${health_status}"
            echo -e "${YELLOW}Возможно API не запущен или ещё не готов принимать запросы${NC}"
        fi
    fi

    # Проверка что порт 4001 НЕ открыт наружу
    if ss -tlnp 2>/dev/null | grep -qE "0\.0\.0\.0:${AMNEZIA_PORT}|:::${AMNEZIA_PORT}"; then
        warn "ВНИМАНИЕ: Amnezia API слушает все интерфейсы на порту ${AMNEZIA_PORT}!"
        echo -e "${YELLOW}Это небезопасно. Рекомендуется в .env установить:${NC}"
        echo -e "  ${BOLD}FASTIFY_ROUTES=${AMNEZIA_HOST}:${AMNEZIA_PORT}${NC}"
    fi
}

# ─────────────────────────────────────────────────────────────
# Вывод результата
# ─────────────────────────────────────────────────────────────
print_result() {
    echo ""
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}${BOLD}  ✨ Публичный HTTPS доступ успешно настроен!${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo ""

    # Получаем API ключ из .env (если есть)
    local API_KEY=""
    local ENV_LOCATIONS=(
        "./.env"
        "$HOME/amnezia-api/.env"
        "/root/amnezia-api/.env"
        "/opt/amnezia-api/.env"
    )

    for env_path in "${ENV_LOCATIONS[@]}"; do
        if [[ -f "$env_path" ]]; then
            API_KEY=$(grep -E "^FASTIFY_API_KEY=" "$env_path" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'" || echo "")
            if [[ -n "$API_KEY" ]]; then
                break
            fi
        fi
    done

    echo -e "${BOLD}🌐 URL для бота:${NC}"
    echo -e "   ${CYAN}https://${DOMAIN}:8443${NC}"
    echo ""

    echo -e "${BOLD}🔑 API-ключ (из .env):${NC}"
    if [[ -n "$API_KEY" ]]; then
        echo -e "   ${CYAN}${API_KEY}${NC}"
        echo ""
        echo -e "   ${YELLOW}⚠️  Никому не показывайте этот ключ!${NC}"
    else
        echo -e "   ${YELLOW}(не удалось найти .env — возьмите ключ вручную)${NC}"
        echo -e "   ${CYAN}cat ~/amnezia-api/.env | grep FASTIFY_API_KEY${NC}"
    fi

    echo ""
    echo -e "${BOLD}🤖 Как добавить сервер в бота:${NC}"
    echo -e "   1. Откройте админку бота → 🌍 Серверы → ➕ Добавить сервер"
    echo -e "   2. Введите имя (например: ${BOLD}Нидерланды${NC})"
    echo -e "   3. Введите флаг (например: ${BOLD}🇳🇱${NC})"
    echo -e "   4. В API URL вставьте: ${CYAN}https://${DOMAIN}:8443${NC}"
    echo -e "   5. В API ключ вставьте ключ выше"
    echo ""

    echo -e "${BOLD}🧪 Ручная проверка (с любой машины):${NC}"
    echo -e "   ${CYAN}curl -H \"x-api-key: ВАШ_КЛЮЧ\" https://${DOMAIN}:8443/server${NC}"
    echo ""

    echo -e "${BOLD}📋 Логи:${NC}"
    echo -e "   • Nginx access:  ${CYAN}tail -f /var/log/nginx/amnezia-api-access.log${NC}"
    echo -e "   • Nginx errors:  ${CYAN}tail -f /var/log/nginx/amnezia-api-error.log${NC}"
    echo -e "   • Setup лог:     ${CYAN}cat ${LOG_FILE}${NC}"
    echo ""

    echo -e "${BOLD}🔧 Полезные команды:${NC}"
    echo -e "   • Проверить сертификат:  ${CYAN}certbot certificates${NC}"
    echo -e "   • Продлить вручную:      ${CYAN}certbot renew${NC}"
    echo -e "   • Перезапустить nginx:   ${CYAN}systemctl reload nginx${NC}"
    echo -e "   • Статус Amnezia API:    ${CYAN}pm2 status${NC}"
    echo ""

    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Готово! Бот в Германии теперь подключится к API в Нидерландах.${NC}"
    echo -e "${GREEN}══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ─────────────────────────────────────────────────────────────
# Uninstall (для отката)
# ─────────────────────────────────────────────────────────────
uninstall() {
    header "🗑 Удаление настройки"

    read -p "Удалить Nginx конфиг и сертификаты для этого домена? (y/N): " -r
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Отменено"
        exit 0
    fi

    rm -f "$NGINX_LINK" "$NGINX_CONF"
    systemctl reload nginx 2>/dev/null || true
    success "Nginx конфиг удалён"

    if [[ -n "${DOMAIN:-}" ]]; then
        certbot delete --cert-name "$DOMAIN" --non-interactive 2>/dev/null || true
        success "Сертификат удалён"
    fi

    exit 0
}

# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
main() {
    mkdir -p /var/log
    echo "=== Amnezia API Public Access Setup: $(date) ===" > "$LOG_FILE"

    echo -e "${GREEN}"
    echo -e "╔══════════════════════════════════════════════════════════════╗"
    echo -e "║                                                              ║"
    echo -e "║   🌐 Amnezia API — Public HTTPS Access Setup                 ║"
    echo -e "║                                                              ║"
    echo -e "║   Настраивает:                                               ║"
    echo -e "║   • Nginx reverse proxy → 127.0.0.1:4001                     ║"
    echo -e "║   • SSL сертификат Let's Encrypt                             ║"
    echo -e "║   • Rate limiting (защита от брутфорса)                      ║"
    echo -e "║   • Автопродление сертификата                                ║"
    echo -e "║   • Скрытие /docs и /metrics                                 ║"
    echo -e "║                                                              ║"
    echo -e "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    parse_args "$@"

    # Быстрый доступ к uninstall
    if [[ "${1:-}" == "--uninstall" ]]; then
        uninstall
    fi

    preflight_checks
    collect_domain_and_email
    install_dependencies
    setup_firewall
    create_initial_nginx_config
    obtain_ssl_certificate
    create_final_nginx_config
    setup_auto_renewal
    final_verification
    print_result
}

main "$@"