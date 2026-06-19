#!/usr/bin/env bash

# ==============================================================================
# VPN Telegram Bot - Auto-Installer & Manager Script (MVP Premium)
# ==============================================================================
# Supports: Ubuntu 20.04 / 22.04 / 24.04 and Debian 11 / 12
# Features:
#   - Interactive menu (Install/Uninstall/Manage/Exit)
#   - Upgrades & installs system requirements (Python 3.10+, pip, build tools)
#   - Reads existing .env defaults on update to prevent re-typing configuration
#   - Automated Xray-core downloader/installer from official XTLS releases
#   - Non-root dedicated security system user configuration (vpnbot)
#   - Systemd service integration for automatic start and monitoring
#   - Optional Nginx reverse proxy + automated Certbot SSL (Let's Encrypt)
#   - Automated database backup on uninstall
# ==============================================================================

# Exit on error
set -e

# Terminal Colors & Styling
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Helper functions for clean output
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}
success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}
warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}
error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Run as root check
if [ "$EUID" -ne 0 ]; then
    error "This script must be run as root or with sudo privileges."
    exit 1
fi

# Function to upgrade the bot using git pull
upgrade_bot() {
    local target_dir=$1
    target_dir=${target_dir:-"/opt/vpn-bot"}

    echo -e "${CYAN}${BOLD}"
    echo "========================================================"
    echo "    VPN Telegram Bot - Upgrading                        "
    echo "========================================================"
    echo -e "${NC}"

    if [ ! -d "$target_dir" ]; then
        error "No installation found at $target_dir. Cannot upgrade."
        return 1
    fi

    if [ ! -d "$target_dir/.git" ]; then
        warn "No Git repository found at $target_dir. Quick upgrade is not possible."
        read -p "Would you like to run a full re-install instead? (y/N): " RUN_FULL
        if [[ "$RUN_FULL" =~ ^[Yy]$ ]]; then
            rm -rf "$target_dir"
            install_bot
            return 0
        else
            error "Upgrade aborted."
            return 1
        fi
    fi

    info "Navigating to $target_dir..."
    cd "$target_dir"

    # Handle local changes
    if ! git diff-index --quiet HEAD --; then
        warn "You have local changes in the bot directory."
        echo "Please choose how to proceed:"
        echo "  1) Temporarily stash your changes, pull code, then re-apply them (Recommended)"
        echo "  2) Overwrite/discard your local changes and force update"
        echo "  3) Abort update"
        read -p "Select option (1-3) [1]: " LOCAL_CHANGES_CHOICE
        LOCAL_CHANGES_CHOICE=${LOCAL_CHANGES_CHOICE:-"1"}

        if [ "$LOCAL_CHANGES_CHOICE" = "1" ]; then
            info "Stashing local changes..."
            git stash
        elif [ "$LOCAL_CHANGES_CHOICE" = "2" ]; then
            info "Discarding local changes..."
            git reset --hard HEAD
        else
            error "Update aborted."
            return 1
        fi
    fi

    info "Pulling latest changes from Github..."
    if ! git pull; then
        error "Failed to pull latest changes. Please check internet connection or Git access."
        # Restore stash if we stashed
        if git stash list | grep -q "stash@{0}"; then
            info "Restoring stashed changes..."
            git stash pop || true
        fi
        return 1
    fi

    # Pop stash if we stashed
    if git stash list | grep -q "stash@{0}"; then
        info "Applying stashed changes..."
        if ! git stash pop; then
            warn "Conflicts occurred while re-applying local changes. Please resolve them manually."
        fi
    fi

    # Upgrade dependencies
    info "Upgrading python dependencies..."
    if [ -f "$target_dir/.venv/bin/pip" ]; then
        "$target_dir/.venv/bin/pip" install --upgrade pip
        "$target_dir/.venv/bin/pip" install -r "$target_dir/requirements.txt"
    else
        warn "Virtual environment not found. Setting up fresh virtual environment..."
        python3 -m venv "$target_dir/.venv"
        "$target_dir/.venv/bin/pip" install --upgrade pip
        "$target_dir/.venv/bin/pip" install -r "$target_dir/requirements.txt"
    fi

    # Fix permissions
    chown -R vpnbot:vpnbot "$target_dir"
    chmod -R 750 "$target_dir"
    if [ -f "$target_dir/vpnbot.db" ]; then
        chmod 660 "$target_dir/vpnbot.db"
    fi

    # Restart service
    info "Restarting vpn-bot service..."
    systemctl daemon-reload
    systemctl restart vpn-bot

    sleep 2
    if systemctl is-active --quiet vpn-bot; then
        success "VPN Telegram Bot updated and restarted successfully!"
    else
        error "Service failed to start after update. Check logs using 'journalctl -u vpn-bot -n 50'."
        return 1
    fi
}

# Function to configure the bot (creates/updates .env)
configure_bot() {
    local target_dir=$1
    target_dir=${target_dir:-"/opt/vpn-bot"}

    echo -e "${CYAN}${BOLD}"
    echo "========================================================"
    echo "    VPN Telegram Bot - Configuration Wizard            "
    echo "========================================================"
    echo -e "${NC}"

    if [ ! -d "$target_dir" ]; then
        error "No installation found at $target_dir. Cannot configure."
        return 1
    fi

    # Load old config values if present to serve as wizard defaults
    local DEF_BOT_TOKEN=""
    local DEF_ADMIN_IDS=""
    local DEF_CONNECT_MODE="DIRECT"
    local DEF_PROXY_URL=""
    local DEF_XRAY_CONFIG_URL=""
    local DEF_XRAY_BIN="xray"
    local DEF_XRAY_SOCKS_PORT="10808"
    local DEF_DEFAULT_LANG="en"
    local DEF_SUPPORT_CONTACT="@your_support"
    local DEF_BRAND_NAME="My VPN"
    local DEF_CARD_NUMBER="6037-9911-1111-1111"
    local DEF_CARD_HOLDER="John Doe"
    local DEF_FIAT_CURRENCY="IRR"
    local DEF_STARS_ENABLED="true"
    local DEF_CRYPTO_ENABLED="false"
    local DEF_NOWPAYMENTS_API_KEY=""
    local DEF_NOWPAYMENTS_IPN_SECRET=""
    local DEF_PUBLIC_BASE_URL=""
    local DEF_WEBHOOK_PORT="8080"

    local old_env=""
    if [ -f "$target_dir/.env" ]; then
        old_env=$(cat "$target_dir/.env")
    fi

    if [ -n "$old_env" ]; then
        info "Found existing configuration. Loading defaults..."
        # Parse values safely
        eval_var() {
            local val=$(echo "$old_env" | grep "^$1=" | cut -d'=' -f2- | tr -d '\r')
            echo "$val"
        }
        DEF_BOT_TOKEN=$(eval_var "BOT_TOKEN")
        DEF_ADMIN_IDS=$(eval_var "ADMIN_IDS")
        DEF_CONNECT_MODE=$(eval_var "CONNECT_MODE")
        DEF_PROXY_URL=$(eval_var "PROXY_URL")
        DEF_XRAY_CONFIG_URL=$(eval_var "XRAY_CONFIG_URL")
        DEF_XRAY_BIN=$(eval_var "XRAY_BIN")
        DEF_XRAY_SOCKS_PORT=$(eval_var "XRAY_SOCKS_PORT")
        DEF_DEFAULT_LANG=$(eval_var "DEFAULT_LANG")
        DEF_SUPPORT_CONTACT=$(eval_var "SUPPORT_CONTACT")
        DEF_BRAND_NAME=$(eval_var "BRAND_NAME")
        DEF_CARD_NUMBER=$(eval_var "CARD_NUMBER")
        DEF_CARD_HOLDER=$(eval_var "CARD_HOLDER")
        DEF_FIAT_CURRENCY=$(eval_var "FIAT_CURRENCY")
        DEF_STARS_ENABLED=$(eval_var "STARS_ENABLED")
        DEF_CRYPTO_ENABLED=$(eval_var "CRYPTO_ENABLED")
        DEF_NOWPAYMENTS_API_KEY=$(eval_var "NOWPAYMENTS_API_KEY")
        DEF_NOWPAYMENTS_IPN_SECRET=$(eval_var "NOWPAYMENTS_IPN_SECRET")
        DEF_PUBLIC_BASE_URL=$(eval_var "PUBLIC_BASE_URL")
        DEF_WEBHOOK_PORT=$(eval_var "WEBHOOK_PORT")
    fi

    # Run configuration prompts
    echo -e "${YELLOW}Please enter configuration details (Press Enter to keep default/current):${NC}"
    
    local BOT_TOKEN=""
    while [ -z "$BOT_TOKEN" ]; do
        read -p "Telegram Bot Token [$DEF_BOT_TOKEN]: " BOT_TOKEN
        BOT_TOKEN=${BOT_TOKEN:-$DEF_BOT_TOKEN}
        if [ -z "$BOT_TOKEN" ]; then
            error "Bot Token is required!"
        fi
    done

    local ADMIN_IDS=""
    while [ -z "$ADMIN_IDS" ]; do
        read -p "Admin Telegram IDs (comma-separated, e.g. 12345,67890) [$DEF_ADMIN_IDS]: " ADMIN_IDS
        ADMIN_IDS=${ADMIN_IDS:-$DEF_ADMIN_IDS}
        if [ -z "$ADMIN_IDS" ]; then
            error "At least one Admin ID is required!"
        fi
    done

    local DEFAULT_LANG=""
    read -p "Default Bot Language (en/fa) [$DEF_DEFAULT_LANG]: " DEFAULT_LANG
    DEFAULT_LANG=${DEFAULT_LANG:-$DEF_DEFAULT_LANG}

    local SUPPORT_CONTACT=""
    read -p "Support Contact Username (e.g. @my_support) [$DEF_SUPPORT_CONTACT]: " SUPPORT_CONTACT
    SUPPORT_CONTACT=${SUPPORT_CONTACT:-$DEF_SUPPORT_CONTACT}

    local BRAND_NAME=""
    read -p "Brand Name (shown in bot UI) [$DEF_BRAND_NAME]: " BRAND_NAME
    BRAND_NAME=${BRAND_NAME:-$DEF_BRAND_NAME}

    local CARD_NUMBER=""
    read -p "Card Number for Payments [$DEF_CARD_NUMBER]: " CARD_NUMBER
    CARD_NUMBER=${CARD_NUMBER:-$DEF_CARD_NUMBER}

    local CARD_HOLDER=""
    read -p "Card Holder Name [$DEF_CARD_HOLDER]: " CARD_HOLDER
    CARD_HOLDER=${CARD_HOLDER:-$DEF_CARD_HOLDER}

    local FIAT_CURRENCY=""
    read -p "Currency Label (e.g. IRR, USD, EUR) [$DEF_FIAT_CURRENCY]: " FIAT_CURRENCY
    FIAT_CURRENCY=${FIAT_CURRENCY:-$DEF_FIAT_CURRENCY}

    # Connection Mode configuration
    echo ""
    echo "Telegram Connection Mode:"
    echo "1) DIRECT (Direct connection to Telegram API)"
    echo "2) PROXY (Route requests through HTTP/Socks5 proxy)"
    echo "3) XRAY (Route requests through local xray-core client)"
    local CONN_CHOICE=""
    read -p "Select Mode (1-3) [DIRECT]: " CONN_CHOICE
    
    local CONNECT_MODE="DIRECT"
    local PROXY_URL=""
    local XRAY_CONFIG_URL=""
    local XRAY_BIN=""
    local XRAY_SOCKS_PORT=""
    if [ "$CONN_CHOICE" = "2" ] || [ "$DEF_CONNECT_MODE" = "PROXY" -a -z "$CONN_CHOICE" ]; then
        CONNECT_MODE="PROXY"
        read -p "Proxy URL (e.g. socks5://127.0.0.1:1080) [$DEF_PROXY_URL]: " PROXY_URL
        PROXY_URL=${PROXY_URL:-$DEF_PROXY_URL}
    elif [ "$CONN_CHOICE" = "3" ] || [ "$DEF_CONNECT_MODE" = "XRAY" -a -z "$CONN_CHOICE" ]; then
        CONNECT_MODE="XRAY"
        read -p "Xray Link Config URL (vless/vmess/trojan/ss) [$DEF_XRAY_CONFIG_URL]: " XRAY_CONFIG_URL
        XRAY_CONFIG_URL=${XRAY_CONFIG_URL:-$DEF_XRAY_CONFIG_URL}
        
        # Check if xray is on system
        if ! command -v xray &>/dev/null && [ ! -f "/usr/local/bin/xray" ]; then
            warn "xray-core binary not found."
            local AUTO_XRAY=""
            read -p "Would you like to automatically download and install official xray-core? (Y/n): " AUTO_XRAY
            AUTO_XRAY=${AUTO_XRAY:-"Y"}
            if [[ "$AUTO_XRAY" =~ ^[Yy]$ ]]; then
                info "Detecting CPU architecture..."
                local ARCH=$(uname -m)
                local XRAY_ARCH=""
                if [ "$ARCH" = "x86_64" ]; then
                    XRAY_ARCH="64"
                elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
                    XRAY_ARCH="arm64-v8a"
                else
                    XRAY_ARCH="32"
                fi
                
                # Fetch latest release binary URL
                local LATEST_XRAY_URL=$(curl -s https://api.github.com/repos/XTLS/Xray-core/releases/latest | grep "browser_download_url" | grep "linux-${XRAY_ARCH}.zip" | head -n 1 | cut -d '"' -f 4)
                if [ -n "$LATEST_XRAY_URL" ]; then
                    info "Downloading Xray-core..."
                    curl -L -o /tmp/xray.zip "$LATEST_XRAY_URL"
                    mkdir -p /tmp/xray_extract
                    unzip -q /tmp/xray.zip -d /tmp/xray_extract
                    mv /tmp/xray_extract/xray /usr/local/bin/xray
                    chmod +x /usr/local/bin/xray
                    rm -rf /tmp/xray.zip /tmp/xray_extract
                    success "Xray-core installed successfully to /usr/local/bin/xray"
                    XRAY_BIN="/usr/local/bin/xray"
                else
                    error "Could not fetch Xray-core release. Please install it manually and set XRAY_BIN."
                    XRAY_BIN="xray"
                fi
            else
                XRAY_BIN="xray"
            fi
        else
            XRAY_BIN="xray"
        fi
        
        read -p "Xray local SOCKS port [$DEF_XRAY_SOCKS_PORT]: " XRAY_SOCKS_PORT
        XRAY_SOCKS_PORT=${XRAY_SOCKS_PORT:-$DEF_XRAY_SOCKS_PORT}
    fi

    # Crypto payment configuration
    local CRYPTO_CHOICE=""
    read -p "Enable NowPayments Crypto Payments? (y/N) [$DEF_CRYPTO_ENABLED]: " CRYPTO_CHOICE
    CRYPTO_CHOICE=${CRYPTO_CHOICE:-$DEF_CRYPTO_ENABLED}
    
    local CRYPTO_ENABLED="false"
    local NOWPAYMENTS_API_KEY=""
    local NOWPAYMENTS_IPN_SECRET=""
    local PUBLIC_BASE_URL=""
    if [[ "$CRYPTO_CHOICE" =~ ^[Yy]$ || "$CRYPTO_CHOICE" = "true" ]]; then
        CRYPTO_ENABLED="true"
        read -p "NowPayments API Key [$DEF_NOWPAYMENTS_API_KEY]: " NOWPAYMENTS_API_KEY
        NOWPAYMENTS_API_KEY=${NOWPAYMENTS_API_KEY:-$DEF_NOWPAYMENTS_API_KEY}
        
        read -p "NowPayments IPN Secret [$DEF_NOWPAYMENTS_IPN_SECRET]: " NOWPAYMENTS_IPN_SECRET
        NOWPAYMENTS_IPN_SECRET=${NOWPAYMENTS_IPN_SECRET:-$DEF_NOWPAYMENTS_IPN_SECRET}
        
        read -p "Public Domain URL for IPN callbacks (e.g. https://bot.yourdomain.com) [$DEF_PUBLIC_BASE_URL]: " PUBLIC_BASE_URL
        PUBLIC_BASE_URL=${PUBLIC_BASE_URL:-$DEF_PUBLIC_BASE_URL}
    fi

    local WEBHOOK_PORT=""
    read -p "Webhook/IPN Local Bind Port [8080]: " WEBHOOK_PORT
    WEBHOOK_PORT=${WEBHOOK_PORT:-$DEF_WEBHOOK_PORT}

    # Save to .env file
    info "Creating/updating .env configuration file..."
    cat << EOF > "$target_dir/.env"
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
CONNECT_MODE=$CONNECT_MODE
PROXY_MODE=OFF
PROXY_URL=$PROXY_URL
XRAY_CONFIG_URL=$XRAY_CONFIG_URL
XRAY_BIN=${XRAY_BIN:-"xray"}
XRAY_SOCKS_PORT=${XRAY_SOCKS_PORT:-"10808"}
DATABASE_URL=sqlite+aiosqlite:///$target_dir/vpnbot.db
DEFAULT_LANG=$DEFAULT_LANG
SUPPORT_CONTACT=$SUPPORT_CONTACT
BRAND_NAME=$BRAND_NAME
CARD_NUMBER=$CARD_NUMBER
CARD_HOLDER=$CARD_HOLDER
FIAT_CURRENCY=$FIAT_CURRENCY
STARS_ENABLED=true
CRYPTO_ENABLED=$CRYPTO_ENABLED
NOWPAYMENTS_API_KEY=$NOWPAYMENTS_API_KEY
NOWPAYMENTS_IPN_SECRET=$NOWPAYMENTS_IPN_SECRET
PUBLIC_BASE_URL=$PUBLIC_BASE_URL
WEBHOOK_HOST=127.0.0.1
WEBHOOK_PORT=$WEBHOOK_PORT
EOF

    # Fix directory permissions
    if id "vpnbot" &>/dev/null; then
        chown -R vpnbot:vpnbot "$target_dir"
    fi
    chmod -R 750 "$target_dir"
    if [ -f "$target_dir/vpnbot.db" ]; then
        if id "vpnbot" &>/dev/null; then
            chown vpnbot:vpnbot "$target_dir/vpnbot.db"
        fi
        chmod 660 "$target_dir/vpnbot.db"
    fi

    success "Configuration file .env successfully updated."

    # If the systemd service exists, restart it and check status
    if systemctl list-unit-files | grep -q "vpn-bot.service"; then
        info "Restarting vpn-bot service to apply new configuration..."
        systemctl daemon-reload
        systemctl restart vpn-bot
        sleep 3
        if systemctl is-active --quiet vpn-bot; then
            success "vpn-bot service is active and running with new configuration!"
        else
            error "vpn-bot service failed to start. Run 'journalctl -u vpn-bot' for logs."
            return 1
        fi
    fi
}

# Function to run installation
install_bot() {
    echo -e "${CYAN}${BOLD}"
    echo "========================================================"
    echo "    VPN Telegram Bot - Installing                       "
    echo "========================================================"
    echo -e "${NC}"

    # 1. System & OS Check
    info "Checking system prerequisites..."
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$NAME
        VER=$VERSION_ID
    else
        OS=$(uname -s)
        VER=""
    fi

    info "Operating System detected: $OS ($VER)"

    if [[ ! "$OS" =~ "Ubuntu" && ! "$OS" =~ "Debian" ]]; then
        warn "This script was optimized for Ubuntu and Debian. Proceeding with standard package manager tools..."
    fi

    # 2. Package Updates & Installation of core packages
    info "Updating system packages..."
    apt-get update -y

    info "Installing core system tools (git, curl, software-properties-common, unzip, sqlite3, build-essential)..."
    apt-get install -y git curl software-properties-common unzip sqlite3 build-essential

    # 3. Python 3 Check & Setup
    info "Checking Python 3 version..."
    PYTHON_CMD="python3"
    if command -v python3 &>/dev/null; then
        PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        info "Python version detected: $PY_VER"
        # If python is older than 3.10 and we are on Ubuntu, add deadsnakes PPA
        if (( $(echo "$PY_VER < 3.10" | bc -l) )); then
            if [[ "$OS" =~ "Ubuntu" ]]; then
                warn "Python version is less than 3.10. Installing Python 3.10 from deadsnakes PPA..."
                add-apt-repository ppa:deadsnakes/ppa -y
                apt-get update -y
                apt-get install -y python3.10 python3.10-venv python3.10-dev
                PYTHON_CMD="python3.10"
            else
                warn "Python version is less than 3.10. Please ensure a newer python version is available. Attempting standard upgrade..."
                apt-get install -y python3 python3-venv python3-dev
            fi
        fi
    else
        info "Python 3 is not installed. Installing Python 3 and virtualenv package..."
        apt-get install -y python3 python3-venv python3-dev
    fi

    # Ensure python3-venv is installed
    info "Installing Python venv package..."
    apt-get install -y python3-venv

    # 4. Clone Repository
    echo ""
    echo -e "${BOLD}1. Repository & Installation Path Config${NC}"
    echo "--------------------------------------------------------"
    read -p "GitHub Repository URL [https://github.com/RootedOne/gbot.git]: " REPO_URL
    REPO_URL=${REPO_URL:-"https://github.com/RootedOne/gbot.git"}

    read -p "Installation Directory [/opt/vpn-bot]: " INSTALL_DIR
    INSTALL_DIR=${INSTALL_DIR:-"/opt/vpn-bot"}

    # Check if directory already exists and handle overwrite
    if [ -d "$INSTALL_DIR" ]; then
        warn "Directory $INSTALL_DIR already exists."
        echo -e "${YELLOW}An existing installation was detected. Please choose how to proceed:${NC}"
        echo "  1) Quick Update (pulls latest code via Git, updates dependencies, restarts service, preserves your DB and config)"
        echo "  2) Full Re-install (wipes the directory, re-clones, and re-runs the configuration wizard)"
        echo "  3) Cancel"
        read -p "Select option (1-3) [1]: " UPGRADE_CHOICE
        UPGRADE_CHOICE=${UPGRADE_CHOICE:-"1"}

        if [ "$UPGRADE_CHOICE" = "1" ]; then
            upgrade_bot "$INSTALL_DIR"
            return 0
        elif [ "$UPGRADE_CHOICE" = "2" ]; then
            # Backup database if we are overwriting an existing installation
            DB_BACKUP_TEMP=""
            if [ -f "$INSTALL_DIR/vpnbot.db" ]; then
                warn "Creating temporary database backup..."
                DB_BACKUP_TEMP="/tmp/vpnbot_db_$(date +%s).bak"
                cp "$INSTALL_DIR/vpnbot.db" "$DB_BACKUP_TEMP"
            fi
            
            # Backup environment configuration if it exists
            ENV_BACKUP_TEMP=""
            if [ -f "$INSTALL_DIR/.env" ]; then
                warn "Creating temporary environment backup..."
                ENV_BACKUP_TEMP="/tmp/vpnbot_env_$(date +%s).bak"
                cp "$INSTALL_DIR/.env" "$ENV_BACKUP_TEMP"
            fi
            info "Removing existing files..."
            rm -rf "$INSTALL_DIR"
        else
            error "Installation aborted by user."
            exit 1
        fi
    fi

    # Clone repository
    info "Cloning repository: $REPO_URL to $INSTALL_DIR..."
    if ! git clone "$REPO_URL" "$INSTALL_DIR"; then
        error "Failed to clone repository. If private, check your credentials."
        exit 1
    fi

    # Restore DB if backup existed
    if [ -n "$DB_BACKUP_TEMP" ] && [ -f "$DB_BACKUP_TEMP" ]; then
        info "Restoring database backup..."
        cp "$DB_BACKUP_TEMP" "$INSTALL_DIR/vpnbot.db"
        rm -f "$DB_BACKUP_TEMP"
    fi

    # Restore environment configuration if backup existed
    if [ -n "$ENV_BACKUP_TEMP" ] && [ -f "$ENV_BACKUP_TEMP" ]; then
        info "Restoring environment configuration backup..."
        cp "$ENV_BACKUP_TEMP" "$INSTALL_DIR/.env"
        rm -f "$ENV_BACKUP_TEMP"
    fi

    # Ensure crucial files exist
    if [ ! -f "$INSTALL_DIR/requirements.txt" ]; then
        error "The cloned repository does not contain requirements.txt."
        exit 1
    fi

    success "Repository files successfully cloned to $INSTALL_DIR."

    # 5. User and Virtual Environment Configuration
    echo ""
    echo -e "${BOLD}2. Security User & Environment Configuration${NC}"
    echo "--------------------------------------------------------"
    # Create dedicated system user
    if id "vpnbot" &>/dev/null; then
        info "User 'vpnbot' already exists."
    else
        info "Creating dedicated system user 'vpnbot'..."
        useradd -r -s /usr/sbin/nologin vpnbot
    fi

    # Configure Virtual Environment
    info "Setting up Python virtual environment..."
    $PYTHON_CMD -m venv "$INSTALL_DIR/.venv"
    source "$INSTALL_DIR/.venv/bin/activate"

    info "Installing dependencies (this may take a few minutes)..."
    pip install --upgrade pip
    pip install -r "$INSTALL_DIR/requirements.txt"

    # 6. Configuration Variables Wizard (.env)
    configure_bot "$INSTALL_DIR"

    # Read variables needed for nginx/firewall setup from the generated .env
    eval_env_var() {
        grep "^$1=" "$INSTALL_DIR/.env" | cut -d'=' -f2- | tr -d '\r'
    }
    PUBLIC_BASE_URL=$(eval_env_var "PUBLIC_BASE_URL")
    WEBHOOK_PORT=$(eval_env_var "WEBHOOK_PORT")

    # 7. Systemd Service setup
    info "Creating Systemd service unit..."
    cat << EOF > /etc/systemd/system/vpn-bot.service
[Unit]
Description=VPN Sell Telegram Bot
After=network.target

[Service]
Type=simple
User=vpnbot
Group=vpnbot
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m bot.main
EnvironmentFile=$INSTALL_DIR/.env
Environment=MPLCONFIGDIR=/tmp/matplotlib
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    info "Enabling and starting vpn-bot service..."
    systemctl daemon-reload
    systemctl enable vpn-bot
    systemctl restart vpn-bot

    # Wait a second for database generation and start
    sleep 3

    # Check status of the service
    if systemctl is-active --quiet vpn-bot; then
        success "vpn-bot service is active and running!"
    else
        error "vpn-bot service failed to start. Run 'journalctl -u vpn-bot' for logs."
        exit 1
    fi

    # 8. Nginx Reverse Proxy and Let's Encrypt Setup (Optional)
    echo ""
    echo -e "${BOLD}3. Web Server & SSL Setup (Nginx + Let's Encrypt)${NC}"
    echo "--------------------------------------------------------"
    read -p "Do you want to configure Nginx reverse proxy with HTTPS? (y/N): " SETUP_NGINX

    if [[ "$SETUP_NGINX" =~ ^[Yy]$ ]]; then
        # Use domain parsed from PUBLIC_BASE_URL if available
        DEF_DOMAIN=$(echo "$PUBLIC_BASE_URL" | awk -F[/:] '{print $4}' || echo "")
        read -p "Enter your Domain Name [$DEF_DOMAIN]: " DOMAIN_NAME
        DOMAIN_NAME=${DOMAIN_NAME:-$DEF_DOMAIN}
        
        if [ -z "$DOMAIN_NAME" ]; then
            error "Domain name cannot be empty. Skipping Nginx setup."
        else
            info "Installing Nginx and Certbot..."
            apt-get install -y nginx certbot python3-certbot-nginx

            info "Creating Nginx configuration file for $DOMAIN_NAME..."
            cat << EOF > "/etc/nginx/sites-available/$DOMAIN_NAME"
server {
    listen 80;
    server_name $DOMAIN_NAME;

    location / {
        proxy_pass http://127.0.0.1:$WEBHOOK_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        proxy_buffering off;
        proxy_read_timeout 60s;
    }
}
EOF

            ln -sf "/etc/nginx/sites-available/$DOMAIN_NAME" "/etc/nginx/sites-enabled/"
            rm -f /etc/nginx/sites-enabled/default || true

            info "Testing Nginx configuration..."
            if nginx -t; then
                systemctl restart nginx
                success "Nginx reverse proxy configured successfully."
                
                info "Obtaining Let's Encrypt SSL Certificate..."
                if certbot --nginx -d "$DOMAIN_NAME" --non-interactive --agree-tos --register-unsafely-without-email; then
                    success "SSL Certificate configured and active."
                else
                    warn "Certbot failed to obtain certificate. Ensure your domain points to this server's public IP."
                fi
            else
                error "Nginx configuration test failed. Reverting proxy activation."
                rm -f "/etc/nginx/sites-enabled/$DOMAIN_NAME"
                systemctl restart nginx
            fi
        fi
    else
        # If not using Nginx, configure systemd to listen on 0.0.0.0 so IPN is reachable
        warn "Nginx proxy skipped. Adjusting vpn-bot local webhook binder to 0.0.0.0..."
        sed -i 's/WEBHOOK_HOST=127.0.0.1/WEBHOOK_HOST=0.0.0.0/g' "$INSTALL_DIR/.env"
        systemctl restart vpn-bot
    fi

    # 9. Firewall Configuration (UFW)
    echo ""
    echo -e "${BOLD}4. Firewall Configuration (UFW)${NC}"
    echo "--------------------------------------------------------"
    read -p "Do you want to configure UFW Firewall rules? (y/N): " SETUP_UFW

    if [[ "$SETUP_UFW" =~ ^[Yy]$ ]]; then
        apt-get install -y ufw

        info "Setting default firewall rules..."
        ufw default deny incoming
        ufw default allow outgoing

        info "Allowing SSH (port 22)..."
        ufw allow 22/tcp

        if [[ "$SETUP_NGINX" =~ ^[Yy]$ ]]; then
            info "Allowing HTTP and HTTPS (ports 80 and 443) for Nginx..."
            ufw allow 80/tcp
            ufw allow 443/tcp
        else
            # Allow the webhook bind port directly
            info "Allowing custom webhook port $WEBHOOK_PORT..."
            ufw allow "$WEBHOOK_PORT"/tcp
        fi

        info "Enabling UFW..."
        echo "y" | ufw enable
        success "Firewall is configured and active."
    fi

    echo ""
    echo -e "${GREEN}${BOLD}========================================================"
    echo "    VPN Telegram Bot Setup Complete!                   "
    echo "========================================================"
    echo -e "${NC}"
    echo -e "Your bot is now running as a Systemd service."
    echo -e "You can manage the bot service using option 3 of this script."
    echo "--------------------------------------------------------"
    echo "Check logs: journalctl -u vpn-bot -n 50 -f"
    echo "Status command: systemctl status vpn-bot"
    echo "========================================================"
    echo ""
}

# Function to run uninstallation
uninstall_bot() {
    echo -e "${RED}${BOLD}"
    echo "========================================================"
    echo "    VPN Telegram Bot - Uninstalling                     "
    echo "========================================================"
    echo -e "${NC}"

    read -p "Are you absolutely sure you want to completely uninstall the Bot service? (y/N): " CONFIRM_UNINSTALL
    if [[ ! "$CONFIRM_UNINSTALL" =~ ^[Yy]$ ]]; then
        info "Uninstallation aborted."
        exit 0
    fi

    # 1. Detect service and paths
    INSTALL_DIR=""
    WEBHOOK_PORT=""
    if systemctl list-unit-files | grep -q "vpn-bot.service"; then
        info "Stopping vpn-bot service..."
        systemctl stop vpn-bot || true
        
        info "Disabling vpn-bot service..."
        systemctl disable vpn-bot || true
        
        # Extract directory from systemd file
        INSTALL_DIR=$(grep WorkingDirectory /etc/systemd/system/vpn-bot.service | awk -F'=' '{print $2}' || echo "")
        
        # Extract port from env file
        if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.env" ]; then
            WEBHOOK_PORT=$(grep WEBHOOK_PORT "$INSTALL_DIR/.env" | awk -F'=' '{print $2}' || echo "")
        fi

        info "Removing systemd service file..."
        rm -f /etc/systemd/system/vpn-bot.service
        systemctl daemon-reload
        success "Systemd service removed."
    else
        warn "Systemd service 'vpn-bot.service' not found."
    fi

    INSTALL_DIR=${INSTALL_DIR:-"/opt/vpn-bot"}

    # 2. Database Backup
    if [ -f "$INSTALL_DIR/vpnbot.db" ]; then
        read -p "Would you like to backup the SQLite database (vpnbot.db) to /root/vpnbot.db.bak? (Y/n): " BACKUP_DB
        BACKUP_DB=${BACKUP_DB:-"Y"}
        if [[ "$BACKUP_DB" =~ ^[Yy]$ ]]; then
            cp "$INSTALL_DIR/vpnbot.db" "/root/vpnbot.db.bak"
            success "Database backed up to /root/vpnbot.db.bak."
        fi
    fi

    # 3. Remove files
    if [ -d "$INSTALL_DIR" ]; then
        info "Deleting installation folder: $INSTALL_DIR..."
        rm -rf "$INSTALL_DIR"
        success "Installation folder deleted."
    else
        warn "Installation directory '$INSTALL_DIR' not found."
    fi

    # 4. Remove system user
    if id "vpnbot" &>/dev/null; then
        info "Deleting system user 'vpnbot'..."
        userdel vpnbot || true
        success "System user 'vpnbot' deleted."
    else
        warn "System user 'vpnbot' not found."
    fi

    # 5. Remove Nginx configuration and SSL Certificates
    read -p "Did you set up Nginx reverse proxy / Let's Encrypt for this bot and want to remove it? (y/N): " REMOVE_NGINX
    if [[ "$REMOVE_NGINX" =~ ^[Yy]$ ]]; then
        read -p "Enter the Domain Name used for the proxy (e.g. bot.yourdomain.com): " DOMAIN_NAME
        if [ -n "$DOMAIN_NAME" ]; then
            if [ -f "/etc/nginx/sites-enabled/$DOMAIN_NAME" ]; then
                info "Removing Nginx site configuration..."
                rm -f "/etc/nginx/sites-enabled/$DOMAIN_NAME"
                rm -f "/etc/nginx/sites-available/$DOMAIN_NAME"
                systemctl restart nginx || true
                success "Nginx proxy configuration removed."
            else
                warn "Nginx configuration for $DOMAIN_NAME not found."
            fi

            if command -v certbot &>/dev/null; then
                info "Deleting Let's Encrypt SSL certificate for $DOMAIN_NAME..."
                certbot delete --cert-name "$DOMAIN_NAME" --non-interactive || true
                success "SSL certificate removed."
            fi
        else
            error "Domain name cannot be empty. Skipping Nginx/SSL cleanup."
        fi
    fi

    # 6. Clean up Firewall rules
    if command -v ufw &>/dev/null && ufw status | grep -q "active"; then
        read -p "Do you want to clean up UFW firewall rules created for this bot? (y/N): " CLEAN_UFW
        if [[ "$CLEAN_UFW" =~ ^[Yy]$ ]]; then
            if [ -n "$WEBHOOK_PORT" ]; then
                info "Removing UFW rule for port $WEBHOOK_PORT..."
                ufw delete allow "$WEBHOOK_PORT"/tcp || true
            fi
            
            warn "Note: HTTP (80) and HTTPS (443) firewall rules might be used by other applications."
            read -p "Do you still want to delete Nginx HTTP/HTTPS rules? (y/N): " CLEAN_HTTP_RULES
            if [[ "$CLEAN_HTTP_RULES" =~ ^[Yy]$ ]]; then
                ufw delete allow 80/tcp || true
                ufw delete allow 443/tcp || true
                success "Nginx firewall rules removed."
            fi
        fi
    fi

    echo ""
    echo -e "${GREEN}${BOLD}========================================================"
    echo "    Uninstall Complete!                                 "
    echo "========================================================"
    echo -e "${NC}"
    echo "All VPN Telegram Bot components have been completely uninstalled."
    echo "========================================================"
    echo ""
}

# Function to run management options
manage_bot() {
    while true; do
        echo ""
        echo -e "${CYAN}${BOLD}VPN Telegram Bot Management Panel${NC}"
        echo "--------------------------------------------------------"
        echo "1) Restart Bot Service"
        echo "2) Stop Bot Service"
        echo "3) Start Bot Service"
        echo "4) View Bot Service Status"
        echo "5) View Real-Time Service Logs (Press Ctrl+C to exit logs)"
        echo "6) Back to Main Menu"
        echo "--------------------------------------------------------"
        read -p "Please select an option (1-6): " MNG_CHOICE

        case $MNG_CHOICE in
            1)
                info "Restarting vpn-bot..."
                systemctl restart vpn-bot
                success "Restart command sent."
                ;;
            2)
                info "Stopping vpn-bot..."
                systemctl stop vpn-bot
                success "Stop command sent."
                ;;
            3)
                info "Starting vpn-bot..."
                systemctl start vpn-bot
                success "Start command sent."
                ;;
            4)
                echo "--------------------------------------------------------"
                systemctl status vpn-bot || true
                echo "--------------------------------------------------------"
                ;;
            5)
                echo "Showing logs. Press Ctrl+C to return to menu."
                journalctl -u vpn-bot -n 100 -f || true
                ;;
            6)
                break
                ;;
            *)
                error "Invalid option. Please choose between 1 and 6."
                ;;
        esac
    done
}

# Main Execution Flow - Argument Parsing
UNINSTALL_FLAG=false
UPDATE_FLAG=false
CONFIG_FLAG=false

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -u|--uninstall) UNINSTALL_FLAG=true ;;
        --update|--upgrade) UPDATE_FLAG=true ;;
        -c|--config) CONFIG_FLAG=true ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  -u, --uninstall   Directly trigger the uninstaller"
            echo "  --update          Directly trigger the quick update/upgrade flow"
            echo "  -c, --config      Directly trigger the .env configuration wizard"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        *) error "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# If uninstall flag is passed, run directly
if [ "$UNINSTALL_FLAG" = true ]; then
    uninstall_bot
    exit 0
fi

# If update flag is passed, run directly
if [ "$UPDATE_FLAG" = true ]; then
    upgrade_bot "/opt/vpn-bot"
    exit 0
fi

# If config flag is passed, run directly
if [ "$CONFIG_FLAG" = true ]; then
    install_dir=""
    if [ -f "/etc/systemd/system/vpn-bot.service" ]; then
        install_dir=$(grep WorkingDirectory /etc/systemd/system/vpn-bot.service | awk -F'=' '{print $2}' || echo "")
    fi
    install_dir=${install_dir:-"/opt/vpn-bot"}
    configure_bot "$install_dir"
    exit 0
fi

# Otherwise, present interactive menu
while true; do
    echo -e "${CYAN}${BOLD}VPN Telegram Bot Tool Suite${NC}"
    echo "--------------------------------------------------------"
    echo "1) Install / Upgrade VPN Telegram Bot"
    echo "2) Update .env Configuration"
    echo "3) Manage Bot Service (Start/Stop/Logs)"
    echo "4) Uninstall Bot Service"
    echo "5) Exit"
    echo "--------------------------------------------------------"
    read -p "Please select an option (1-5): " CHOICE

    case $CHOICE in
        1)
            install_bot
            break
            ;;
        2)
            install_dir=""
            if [ -f "/etc/systemd/system/vpn-bot.service" ]; then
                install_dir=$(grep WorkingDirectory /etc/systemd/system/vpn-bot.service | awk -F'=' '{print $2}' || echo "")
            fi
            install_dir=${install_dir:-"/opt/vpn-bot"}
            configure_bot "$install_dir"
            ;;
        3)
            manage_bot
            ;;
        4)
            uninstall_bot
            break
            ;;
        5)
            info "Exiting..."
            exit 0
            ;;
        *)
            error "Invalid option. Please choose between 1 and 5."
            echo ""
            ;;
    esac
done
