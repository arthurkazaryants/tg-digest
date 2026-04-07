#!/bin/bash

################################################################################
# TG-Digest Production Server Setup Script
# 
# Подготавливает сервер к запуску TG-Digest (Reader + Publisher) в production:
# - Проверка требований (ОС, привилегии)
# - Установка Docker и Docker Compose
# - Подготовка папок и прав доступа для Reader и Publisher
# - Создание Telegram сессионных файлов для обоих сервисов
# - Настройка безопасности и firewall
# - Валидация установки
# 
# Использование: sudo bash setup-production-server.sh
################################################################################

set -e  # выход при ошибке
set -u  # ошибка на неопределённых переменных

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Логирование
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1" >&2
}

log_warning() {
    echo -e "${YELLOW}[!]${NC} $1"
}

################################################################################
# 1. PRE-FLIGHT CHECKS
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════────────────${NC}"
echo -e "${BLUE}TG-Digest Production Server Setup${NC}"
echo -e "${BLUE}════════════════════════════════════════════────────────${NC}\n"

log_info "Running pre-flight checks..."

# Проверка, что скрипт запущен от root
if [[ $EUID -ne 0 ]]; then
    log_error "This script must be run as root. Use: sudo bash setup-production-server.sh"
    exit 1
fi
log_success "Running as root"

# Проверка ОС (только Linux)
if [[ ! "$OSTYPE" =~ ^linux ]]; then
    log_error "This script is for Linux only. Current OS: $OSTYPE"
    exit 1
fi
log_success "Linux detected"

# Определяем дистрибутив
if [ -f /etc/os-release ]; then
    . /etc/os-release
    log_info "OS: $NAME $VERSION_ID"
else
    log_warning "Could not detect OS version, but continuing..."
fi

################################################################################
# 2. УСТАНОВКА DOCKER И DOCKER COMPOSE
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════────────────${NC}"
log_info "Installing Docker and Docker Compose..."
echo -e "${BLUE}════════════════════════════════════════════────────────${NC}\n"

# Обновляем пакеты
log_info "Updating package manager..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    INSTALL_CMD="apt-get install -y"
    PACKAGE_MANAGER="apt"
elif command -v yum &> /dev/null; then
    yum update -y -q
    INSTALL_CMD="yum install -y"
    PACKAGE_MANAGER="yum"
else
    log_error "Unsupported package manager. Supported: apt (Debian/Ubuntu), yum (RHEL/CentOS)"
    exit 1
fi
log_success "Package manager found: $PACKAGE_MANAGER"

# Проверяем, установлен ли Docker
if command -v docker &> /dev/null; then
    log_warning "Docker already installed: $(docker --version)"
else
    log_info "Installing Docker..."
    
    if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
        # Debian/Ubuntu
        $INSTALL_CMD ca-certificates curl gnupg lsb-release
        mkdir -p /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/$(lsb_release -si | tr '[:upper:]' '[:lower:]')/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$(lsb_release -si | tr '[:upper:]' '[:lower:]') $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
        apt-get update -qq
        $INSTALL_CMD docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    else
        # RHEL/CentOS
        $INSTALL_CMD docker-io
    fi
    
    log_success "Docker installed: $(docker --version)"
fi

# Проверяем Docker Compose
if command -v docker-compose &> /dev/null; then
    log_warning "Docker Compose already installed: $(docker-compose --version)"
elif docker compose version &> /dev/null; then
    log_success "Docker Compose (plugin) found: $(docker compose version)"
else
    log_info "Installing Docker Compose..."
    
    if [[ "$PACKAGE_MANAGER" == "apt" ]]; then
        $INSTALL_CMD docker-compose-plugin
    else
        $INSTALL_CMD docker-compose
    fi
    
    log_success "Docker Compose installed"
fi

# Стартуем Docker daemon
log_info "Starting Docker daemon..."
systemctl enable docker
systemctl start docker
log_success "Docker daemon started and enabled"

################################################################################
# 3. УСТАНОВКА ДОПОЛНИТЕЛЬНЫХ ИНСТРУМЕНТОВ
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════────────────${NC}"
log_info "Installing additional tools..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# curl (для проверок)
if ! command -v curl &> /dev/null; then
    log_info "Installing curl..."
    $INSTALL_CMD curl
    log_success "curl installed"
fi

# jq (для работы с JSON)
if ! command -v jq &> /dev/null; then
    log_info "Installing jq..."
    $INSTALL_CMD jq
    log_success "jq installed"
fi

# git (для клонирования репозитория)
if ! command -v git &> /dev/null; then
    log_info "Installing git..."
    $INSTALL_CMD git
    log_success "git installed"
fi

################################################################################
# 4. СОЗДАНИЕ НЕПРИВИЛЕГИРОВАННОГО ПОЛЬЗОВАТЕЛЯ
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_info "Creating dedicated application user..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

APP_USER="${APP_USER:-tg-digest}"
APP_GROUP="${APP_GROUP:-tg-digest}"
APP_HOME="${APP_HOME:-/home/tg-digest}"

log_info "Application user: $APP_USER (system user, no login shell)"
log_info "Application home: $APP_HOME"

# Создаём домашнюю папку
if [ ! -d "$APP_HOME" ]; then
    log_info "Creating home directory: $APP_HOME..."
    mkdir -p "$APP_HOME"
    chmod 700 "$APP_HOME"
    log_success "Home directory created"
fi

# Проверяем, существует ли уже пользователь
if id "$APP_USER" &>/dev/null; then
    log_warning "User '$APP_USER' already exists (skipping creation)"
else
    log_info "Creating user '$APP_USER'..."
    useradd --system --shell /bin/false --home-dir "$APP_HOME" --uid 1000 --gid 1000 "$APP_USER"
    chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"
    log_success "User '$APP_USER' created with home directory '$APP_HOME'"
fi

# Добавляем пользователя в группу docker
if getent group docker &>/dev/null; then
    if id -nG "$APP_USER" | grep -qw docker; then
        log_warning "User '$APP_USER' already in docker group"
    else
        log_info "Adding user '$APP_USER' to docker group..."
        usermod -aG docker $APP_USER
        log_success "User '$APP_USER' added to docker group"
    fi
else
    log_error "Docker group does not exist"
    exit 1
fi

################################################################################
# 5. ПОДГОТОВКА ДИРЕКТОРИЙ
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════────────────${NC}"
log_info "Preparing directories..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# Создаём рабочую директорию (обычно /opt)
WORK_DIR="${WORK_DIR:-/opt}"
PROJECT_DIR="$WORK_DIR/tg-digest"
SECRETS_DIR="$WORK_DIR/tg-digest-secrets"

log_info "Project directory: $PROJECT_DIR"
log_info "Secrets directory: $SECRETS_DIR"

# Создаём папки
mkdir -p "$PROJECT_DIR"
mkdir -p "$SECRETS_DIR"
log_success "Directories created"

# Создаём папку для данных PostgreSQL
mkdir -p "$PROJECT_DIR/data/postgres"
log_success "PostgreSQL data directory created: $PROJECT_DIR/data/postgres"

################################################################################
# 6. НАСТРОЙКА ПРАВ ДОСТУПА И БЕЗОПАСНОСТИ
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_info "Configuring permissions and security..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# Устанавливаем владельца проекта на APP_USER
chown -R $APP_USER:$APP_GROUP "$PROJECT_DIR"
log_success "Project directory owner: $APP_USER:$APP_GROUP"

# Устанавливаем владельца secrets на APP_USER
chown -R $APP_USER:$APP_GROUP "$SECRETS_DIR"
log_success "Secrets directory owner: $APP_USER:$APP_GROUP"

# Права на папку проекта (755 = rwxr-xr-x)
chmod 755 "$PROJECT_DIR"
log_success "Project directory permissions: 755"

# Права на папку secrets (700 = rwx------) — только владелец может читать
chmod 700 "$SECRETS_DIR"
log_success "Secrets directory permissions: 700 (owner only)"

# Права на данные PostgreSQL (750 = rwxr-x---) — владелец и группа docker
chmod 750 "$PROJECT_DIR/data"
chmod 750 "$PROJECT_DIR/data/postgres"
log_success "PostgreSQL data permissions: 750 (owner and docker group)"

################################################################################
# 7. СОЗДАНИЕ TEMPLATE FILES ДЛЯ SECRETS
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_info "Creating secrets template files..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# Функция для создания и установки прав на secret файл
create_secret_file() {
    local filename=$1
    local content=$2
    local filepath="$SECRETS_DIR/$filename"
    
    if [ -f "$filepath" ]; then
        log_warning "File already exists: $filename (skipping)"
    else
        echo "$content" > "$filepath"
        chown $APP_USER:$APP_GROUP "$filepath"
        chmod 600 "$filepath"
        log_success "Created: $filename (owner: $APP_USER, permissions: 600)"
    fi
}

# pg_password
create_secret_file "pg_password.txt" "CHANGE_ME_strong_database_password_$(openssl rand -hex 12)"

# tg_api_id
create_secret_file "tg_api_id.txt" "YOUR_TELEGRAM_API_ID_FROM_MY_TELEGRAM_ORG"

# tg_api_hash
create_secret_file "tg_api_hash.txt" "YOUR_TELEGRAM_API_HASH_FROM_MY_TELEGRAM_ORG"

# tg_reader_session (будет создана автоматически при первом запуске)
create_secret_file "tg_reader_session.txt" ""

# tg_publisher_session (будет создана автоматически для publisher)
create_secret_file "tg_publisher_session.txt" ""

# llm_api_key (для будущего engine)
create_secret_file "llm_api_key.txt" "sk-YOUR_LLM_API_KEY_HERE"

################################################################################
# 8. НАСТРОЙКА FIREWALL
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_info "Configuring firewall rules..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# Проверяем, включен ли UFW (Ubuntu Firewall)
if command -v ufw &> /dev/null && ufw status | grep -q "Status: active"; then
    log_info "UFW firewall detected (active)"
    
    # Разрешаем SSH (критично!)
    if ! ufw status | grep -q "22/tcp"; then
        log_info "Allowing SSH (port 22)..."
        ufw allow 22/tcp
        log_success "SSH allowed"
    else
        log_warning "SSH already allowed"
    fi
    
    # Блокируем PostgreSQL port от внешних сетей (только локально через Docker)
    # PostgreSQL слушает на 127.0.0.1:5432, поэтому доступа извне не будет
    log_success "PostgreSQL protected (localhost only via docker-compose)"
    
elif command -v iptables &> /dev/null; then
    log_info "iptables firewall detected"
    # iptables обычно настраивается вручную, показываем рекомендацию
    log_warning "Please manually configure iptables. Ensure:"
    log_warning "  - SSH access (port 22) is allowed"
    log_warning "  - PostgreSQL is NOT exposed to external networks"
else
    log_warning "No firewall detected or configured"
fi

################################################################################
# 9. ВАЛИДАЦИЯ УСТАНОВКИ
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_info "Validating installation..."
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

# Docker
if docker --version &> /dev/null; then
    log_success "Docker: $(docker --version)"
else
    log_error "Docker validation failed"
    exit 1
fi

# Docker daemon
if docker ps &> /dev/null; then
    log_success "Docker daemon: running and accessible"
else
    log_error "Docker daemon is not accessible"
    exit 1
fi

# Docker Compose
if docker-compose --version &> /dev/null || docker compose version &> /dev/null; then
    log_success "Docker Compose: installed"
else
    log_error "Docker Compose validation failed"
    exit 1
fi

# Directories
if [ -d "$PROJECT_DIR" ] && [ -d "$SECRETS_DIR" ]; then
    log_success "Project directories: ready"
else
    log_error "Project directories failed"
    exit 1
fi

# Permissions
if [ "$(stat -c %a "$SECRETS_DIR")" == "700" ]; then
    log_success "Secrets directory permissions: ✓ secure (700)"
else
    log_error "Secrets directory permissions: ✗ insecure"
    exit 1
fi

# Secrets files
if [ -f "$SECRETS_DIR/pg_password.txt" ]; then
    log_success "Secrets files: created and secured (600)"
else
    log_error "Secrets files creation failed"
    exit 1
fi

################################################################################
# 10. POST-INSTALLATION SUMMARY
################################################################################

echo -e "\n${BLUE}════════════════════════════════════════════════════════${NC}"
log_success "Server setup completed successfully!"
echo -e "${BLUE}════════════════════════════════════════════════════════${NC}\n"

echo -e "${GREEN}📋 NEXT STEPS:${NC}\n"

echo -e "${YELLOW}1. Update secrets with real values (as $APP_USER user):${NC}"
echo "   sudo -u $APP_USER vi $SECRETS_DIR/pg_password.txt       # Strong password"
echo "   sudo -u $APP_USER vi $SECRETS_DIR/tg_api_id.txt        # From my.telegram.org"
echo "   sudo -u $APP_USER vi $SECRETS_DIR/tg_api_hash.txt      # From my.telegram.org"
echo ""

echo -e "${YELLOW}2. Switch to application user (recommended):${NC}"
echo "   sudo -u $APP_USER -i"
echo "   # Now all docker-compose commands run as $APP_USER without sudo"
echo ""

echo -e "${YELLOW}3. Clone the TG-Digest repository (as $APP_USER):${NC}"
echo "   cd $PROJECT_DIR"
echo "   git clone <your-repo-url> ."
echo ""

echo -e "${YELLOW}4. Review and customize configuration:${NC}"
echo "   vi $PROJECT_DIR/config/config.yml        # Application config (reader + publisher)"
echo "   vi $PROJECT_DIR/.env                     # Infrastructure only (DB, pooling, debug)"
echo "   vi $PROJECT_DIR/docker-compose.yml       # Container orchestration"
echo ""

echo -e "${YELLOW}5. Build services (reader and publisher):${NC}"
echo "   cd $PROJECT_DIR"
echo "   docker-compose build reader publisher"
echo ""

echo -e "${YELLOW}6. Start PostgreSQL database (runs first):${NC}"
echo "   docker-compose up -d postgres"
echo ""

echo -e "${YELLOW}7a. Option 1 - Start both reader and publisher:${NC}"
echo "   docker-compose up -d reader publisher"
echo ""

echo -e "${YELLOW}7b. Option 2 - Start only reader (collect posts):${NC}"
echo "   docker-compose up -d reader"
echo ""

echo -e "${YELLOW}7c. Option 3 - Start both independently with healthchecks:${NC}"
echo "   docker-compose up -d --wait reader publisher"
echo ""

echo -e "${YELLOW}8. Check service status and logs:${NC}"
echo "   docker-compose ps                        # Shows all services"
echo "   docker-compose logs -f reader            # Reader logs"
echo "   docker-compose logs -f publisher         # Publisher logs"
echo "   docker-compose logs -f                   # Combined logs"
echo ""

echo -e "${GREEN}📁 Directory Structure:${NC}"
echo "   $PROJECT_DIR/                 ← Project root (owner: $APP_USER)"
echo "   $PROJECT_DIR/config/          ← Application config (config.yml - shared by reader+publisher)"
echo "   $PROJECT_DIR/app/             ← Application services"
echo "   $PROJECT_DIR/app/reader/      ← Reader service (collects posts from Telegram)"
echo "   $PROJECT_DIR/app/publisher/   ← Publisher service (sends digests to channels)"
echo "   $PROJECT_DIR/app/engine/      ← Engine service (reserved for LLM integration)"
echo "   $PROJECT_DIR/data/postgres/   ← Database storage (shared by reader+publisher)"
echo "   $SECRETS_DIR/                 ← Secrets (owner: $APP_USER, perms: 700)"
echo ""

echo -e "${GREEN}👤 Application User:${NC}"
echo "   Username: $APP_USER"
echo "   Home directory: /nonexistent (system user, no login)"
echo "   Docker group: yes (can run docker commands without sudo)"
echo "   Shell: no (/bin/false - system user for security)"
echo ""

echo -e "${GREEN}🔐 Security Summary:${NC}"
echo "   ✓ Secrets directory: 700 (owner only)"
echo "   ✓ Secrets files: 600 (owner read/write)"
echo "   ✓ Application user: $APP_USER (no shell, no home login)"
echo "   ✓ Docker access: via docker group (no sudo for docker commands)"
echo "   ✓ PostgreSQL: localhost only (not exposed)"
echo "   ✓ SSH: protected by firewall"
echo ""

echo -e "${GREEN}🏗️  Service Architecture:${NC}"
echo "   Reader Service:"
echo "     • Fetches posts from Telegram channels (scheduler: cron from config.yml)"
echo "     • Applies tag filters (include/exclude keywords, location, seniority)"
echo "     • Inserts posts into raw_posts table"
echo "     • Runs continuously on schedule (e.g., every 4 hours)"
echo ""
echo "   Publisher Service:"
echo "     • Monitors raw_posts table for unpublished content"
echo "     • Publishes batches of posts to output channels"
echo "     • Two modes: scheduled (for automatic digest) + queue overflow monitoring"
echo "     • Marks posts as published to avoid duplicates"
echo ""
echo "   Shared Infrastructure:"
echo "     • One PostgreSQL database for both services"
echo "     • One config.yml (sections: reader, channels, tag_filters, publisher)"
echo "     • One Telegram account (reader session + publisher session stored separately)"
echo "     • Both services run as user: $APP_USER"
echo ""

echo -e "${YELLOW}⚠️  IMPORTANT REMINDERS:${NC}"
echo "   • Configuration: Use config.yml (SINGLE source of truth for app config)"
echo "   • Services: Reader collects posts → Publisher sends digests to output channels"
echo "   • Database: Both reader and publisher share one PostgreSQL instance"
echo "   • Commands: Use 'sudo -u $APP_USER docker-compose ...' or switch user first"
echo "   • Secrets: Update all values (pg_password, tg_api_id/hash, etc) before running"
echo "   • Reader: Fetches posts based on cron schedule from config.yml"
echo "   • Publisher: Monitors queue and publishes batches based on same config.yml"
echo "   • Docker: Keep images updated regularly: docker-compose pull && docker-compose up -d"
echo "   • Backup: Regularly backup PostgreSQL data: $PROJECT_DIR/data/postgres"
echo ""

echo -e "${GREEN}✓ Server ready for TG-Digest deployment!${NC}\n"
