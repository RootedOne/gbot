#!/usr/bin/env bash

# ==============================================================================
# VPN Bot Middle Server Relay - Auto-Installer & Uninstaller
# ==============================================================================
# Supports: Ubuntu 20.04 / 22.04 / 24.04 and Debian 11 / 12
# Features:
#   - Interactive menu (Install/Uninstall/Exit)
#   - Automated dependency resolution (Python 3.10+, git, pip, venv)
#   - Non-root dedicated system user configuration
#   - SSL/TLS Nginx Reverse Proxy + Certbot configuration
#   - Automated cleanly-targeted uninstaller (removes service, files, users, 
#     Nginx configs, Certbot certs, and firewall rules)
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

# Function to run installation
install_relay() {
    echo -e "${CYAN}${BOLD}"
    echo "========================================================"
    echo "    VPN Bot Middle Server Relay - Installing            "
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

    info "Installing core system tools (git, curl, software-properties-common)..."
    apt-get install -y git curl software-properties-common

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
    read -p "GitHub Repository URL [https://github.com/rootedOne/relay-server.git]: " REPO_URL
    REPO_URL=${REPO_URL:-"https://github.com/rootedOne/relay-server.git"}

    read -p "Installation Directory [/opt/vpn-relay]: " INSTALL_DIR
    INSTALL_DIR=${INSTALL_DIR:-"/opt/vpn-relay"}

    # Check if directory already exists
    if [ -d "$INSTALL_DIR" ]; then
        warn "Directory $INSTALL_DIR already exists."
        read -p "Do you want to overwrite it? (y/N): " CONFIRM_OVERWRITE
        if [[ "$CONFIRM_OVERWRITE" =~ ^[Yy]$ ]]; then
            info "Removing existing directory..."
            rm -rf "$INSTALL_DIR"
        else
            error "Installation aborted by user."
            exit 1
        fi
    fi

    # Clone repository to a temporary directory
    TEMP_CLONE="/tmp/vpn_relay_clone_$(date +%s)"
    info "Cloning repository: $REPO_URL to temporary path..."
    if ! git clone "$REPO_URL" "$TEMP_CLONE"; then
        error "Failed to clone repository. If the repository is private, verify your credentials or check your connection."
        exit 1
    fi

    # Determine structure and copy files to final destination
    mkdir -p "$INSTALL_DIR"
    if [ -d "$TEMP_CLONE/relay" ]; then
        info "Found 'relay' folder in repository. Copying contents..."
        cp -r "$TEMP_CLONE/relay/"* "$INSTALL_DIR/"
    else
        info "No 'relay' folder found at root. Copying repository files..."
        cp -r "$TEMP_CLONE/"* "$INSTALL_DIR/"
    fi
    rm -rf "$TEMP_CLONE"

    # Ensure crucial files exist
    if [ ! -f "$INSTALL_DIR/main.py" ] || [ ! -f "$INSTALL_DIR/requirements.txt" ]; then
        error "The cloned repository does not contain main.py or requirements.txt in the target location."
        exit 1
    fi

    success "Repository files successfully copied to $INSTALL_DIR."

    # 5. User and Virtual Environment Configuration
    echo ""
    echo -e "${BOLD}2. Security User & Environment Configuration${NC}"
    echo "--------------------------------------------------------"
    # Create dedicated system user
    if id "vpnrelay" &>/dev/null; then
        info "User 'vpnrelay' already exists."
    else
        info "Creating dedicated system user 'vpnrelay'..."
        useradd -r -s /usr/sbin/nologin vpnrelay
    fi

    # Configure Virtual Environment
    info "Setting up Python virtual environment..."
    $PYTHON_CMD -m venv "$INSTALL_DIR/.venv"
    source "$INSTALL_DIR/.venv/bin/activate"

    info "Installing dependencies..."
    pip install --upgrade pip
    pip install -r "$INSTALL_DIR/requirements.txt"

    # 6. Configuration Variables (RELAY_TOKEN, Port)
    # Generate a random 32-char token
    DEFAULT_TOKEN=$(openssl rand -hex 16)

    read -p "Enter Relay Authentication Token (Leave empty to generate randomly) [$DEFAULT_TOKEN]: " USER_TOKEN
    RELAY_TOKEN=${USER_TOKEN:-$DEFAULT_TOKEN}

    read -p "Enter Port to listen on [8000]: " RELAY_PORT
    RELAY_PORT=${RELAY_PORT:-"8000"}

    # Save settings to .env file
    info "Creating .env configuration file..."
    cat << EOF > "$INSTALL_DIR/.env"
RELAY_TOKEN=$RELAY_TOKEN
HOST=127.0.0.1
PORT=$RELAY_PORT
EOF

    # Correct folder permissions
    chown -R vpnrelay:vpnrelay "$INSTALL_DIR"
    chmod -R 750 "$INSTALL_DIR"

    # 7. Systemd Service setup
    info "Creating Systemd service unit..."
    cat << EOF > /etc/systemd/system/vpn-relay.service
[Unit]
Description=VPN Bot Middle Server Relay
After=network.target

[Service]
Type=simple
User=vpnrelay
Group=vpnrelay
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/uvicorn main:app --host 127.0.0.1 --port $RELAY_PORT
EnvironmentFile=$INSTALL_DIR/.env
AmbientCapabilities=CAP_NET_BIND_SERVICE
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    info "Enabling and starting vpn-relay service..."
    systemctl daemon-reload
    systemctl enable vpn-relay
    systemctl restart vpn-relay

    # Wait a second for service to startup
    sleep 2

    # Check status of the service
    if systemctl is-active --quiet vpn-relay; then
        success "vpn-relay service is active and running!"
    else
        error "vpn-relay service failed to start. Run 'journalctl -u vpn-relay' for logs."
        exit 1
    fi

    # 8. Nginx Reverse Proxy and Let's Encrypt Setup (Optional)
    echo ""
    echo -e "${BOLD}3. Web Server & SSL Setup (Nginx + Let's Encrypt)${NC}"
    echo "--------------------------------------------------------"
    read -p "Do you want to configure Nginx reverse proxy with HTTPS? (y/N): " SETUP_NGINX

    if [[ "$SETUP_NGINX" =~ ^[Yy]$ ]]; then
        read -p "Enter your Domain Name (e.g. relay.yourdomain.com): " DOMAIN_NAME
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
        proxy_pass http://127.0.0.1:$RELAY_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Disable buffering for real-time proxies
        proxy_buffering off;
        proxy_read_timeout 60s;
    }
}
EOF

            ln -sf "/etc/nginx/sites-available/$DOMAIN_NAME" "/etc/nginx/sites-enabled/"
            rm -f /etc/nginx/sites-enabled/default

            info "Testing Nginx configuration..."
            if nginx -t; then
                systemctl restart nginx
                success "Nginx reverse proxy configured successfully."
                
                info "Obtaining Let's Encrypt SSL Certificate..."
                # Request certbot certificate automatically
                if certbot --nginx -d "$DOMAIN_NAME" --non-interactive --agree-tos --register-unsafely-without-email; then
                    success "SSL Certificate configured and active."
                    RELAY_URL="https://$DOMAIN_NAME"
                else
                    warn "Certbot failed to obtain certificate. Ensure your domain points to this server's public IP. Using HTTP for now."
                    RELAY_URL="http://$DOMAIN_NAME"
                fi
            else
                error "Nginx configuration test failed. Reverting proxy activation."
                rm -f "/etc/nginx/sites-enabled/$DOMAIN_NAME"
                systemctl restart nginx
                RELAY_URL="http://YOUR_SERVER_IP:$RELAY_PORT"
            fi
        fi
    else
        # If not using Nginx, configure systemd to listen on 0.0.0.0 so it is reachable from external IP
        warn "Nginx proxy skipped. Adjusting vpn-relay to listen on 0.0.0.0..."
        sed -i 's/HOST=127.0.0.1/HOST=0.0.0.0/g' "$INSTALL_DIR/.env"
        sed -i 's/--host 127.0.0.1/--host 0.0.0.0/g' /etc/systemd/system/vpn-relay.service
        
        systemctl daemon-reload
        systemctl restart vpn-relay
        
        # Get server IP
        SERVER_IP=$(curl -s https://ifconfig.me || curl -s https://api.ipify.org || echo "YOUR_SERVER_IP")
        RELAY_URL="http://$SERVER_IP:$RELAY_PORT"
    fi

    # 9. Firewall Configuration (UFW)
    echo ""
    echo -e "${BOLD}4. Firewall Configuration (UFW)${NC}"
    echo "--------------------------------------------------------"
    read -p "Do you want to enable/configure UFW Firewall rules? (y/N): " SETUP_UFW

    if [[ "$SETUP_UFW" =~ ^[Yy]$ ]]; then
        # Install UFW if missing
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
            # Allow the custom port
            info "Allowing custom relay port $RELAY_PORT..."
            # Optionally prompt for bot IP
            read -p "Enter the VPN Bot Server IP to ONLY allow connections from that IP (leave empty to allow all): " BOT_IP
            if [ -n "$BOT_IP" ]; then
                ufw allow from "$BOT_IP" to any port "$RELAY_PORT" proto tcp
                success "Restricted port $RELAY_PORT to IP: $BOT_IP"
            else
                ufw allow "$RELAY_PORT"/tcp
                success "Exposed port $RELAY_PORT to the public"
            fi
        fi

        info "Enabling UFW..."
        # Enable UFW non-interactively
        echo "y" | ufw enable
        success "Firewall is configured and active."
    fi

    # Final Summary Report
    echo ""
    echo -e "${GREEN}${BOLD}========================================================"
    echo "    Setup Complete! Here is your relay info:            "
    echo "========================================================"
    echo -e "${NC}"
    echo -e "${BOLD}Relay URL:${NC}        $RELAY_URL"
    echo -e "${BOLD}Relay Token:${NC}      $RELAY_TOKEN"
    echo ""
    echo -e "Use the above URL and Token in your Bot Admin Panel when adding or editing a panel."
    echo "--------------------------------------------------------"
    echo "Verify health check: curl $RELAY_URL/health"
    echo "Systemd Service status: systemctl status vpn-relay"
    echo "========================================================"
    echo ""
}

# Function to run uninstallation
uninstall_relay() {
    echo -e "${RED}${BOLD}"
    echo "========================================================"
    echo "    VPN Bot Middle Server Relay - Uninstalling          "
    echo "========================================================"
    echo -e "${NC}"

    # Confirm action
    read -p "Are you absolutely sure you want to completely uninstall the Relay service? (y/N): " CONFIRM_UNINSTALL
    if [[ ! "$CONFIRM_UNINSTALL" =~ ^[Yy]$ ]]; then
        info "Uninstallation aborted."
        exit 0
    fi

    # 1. Stop and Disable Systemd Service
    if systemctl list-unit-files | grep -q "vpn-relay.service"; then
        info "Stopping vpn-relay service..."
        systemctl stop vpn-relay || true
        
        info "Disabling vpn-relay service..."
        systemctl disable vpn-relay || true
        
        # Extract working directory from the systemd file before deleting it
        INSTALL_DIR=$(grep WorkingDirectory /etc/systemd/system/vpn-relay.service | awk -F'=' '{print $2}' || echo "")
        
        # Extract port from service file or environment file
        RELAY_PORT=$(grep PORT /etc/systemd/system/vpn-relay.service 2>/dev/null | awk -F'--port ' '{print $2}' | awk '{print $1}' || echo "")
        if [ -z "$RELAY_PORT" ] && [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/.env" ]; then
            RELAY_PORT=$(grep PORT "$INSTALL_DIR/.env" | awk -F'=' '{print $2}' || echo "")
        fi

        info "Removing systemd service file..."
        rm -f /etc/systemd/system/vpn-relay.service
        systemctl daemon-reload
        success "Systemd service removed."
    else
        warn "Systemd service 'vpn-relay.service' not found."
    fi

    # Fallback default install dir if not found from service
    INSTALL_DIR=${INSTALL_DIR:-"/opt/vpn-relay"}

    # 2. Remove files
    if [ -d "$INSTALL_DIR" ]; then
        info "Deleting installation folder: $INSTALL_DIR..."
        rm -rf "$INSTALL_DIR"
        success "Installation folder deleted."
    else
        warn "Installation directory '$INSTALL_DIR' not found."
    fi

    # 3. Remove system user
    if id "vpnrelay" &>/dev/null; then
        info "Deleting system user 'vpnrelay'..."
        userdel vpnrelay || true
        success "System user 'vpnrelay' deleted."
    else
        warn "System user 'vpnrelay' not found."
    fi

    # 4. Remove Nginx configuration and SSL Certificates
    read -p "Did you set up Nginx reverse proxy / Let's Encrypt for a domain and want to remove it? (y/N): " REMOVE_NGINX
    if [[ "$REMOVE_NGINX" =~ ^[Yy]$ ]]; then
        read -p "Enter the Domain Name used for the proxy (e.g. relay.yourdomain.com): " DOMAIN_NAME
        if [ -n "$DOMAIN_NAME" ]; then
            # Nginx config removal
            if [ -f "/etc/nginx/sites-enabled/$DOMAIN_NAME" ]; then
                info "Removing Nginx site configuration..."
                rm -f "/etc/nginx/sites-enabled/$DOMAIN_NAME"
                rm -f "/etc/nginx/sites-available/$DOMAIN_NAME"
                systemctl restart nginx || true
                success "Nginx proxy configuration removed."
            else
                warn "Nginx configuration for $DOMAIN_NAME not found."
            fi

            # Certbot SSL certificate removal
            if command -v certbot &>/dev/null; then
                info "Deleting Let's Encrypt SSL certificate for $DOMAIN_NAME..."
                certbot delete --cert-name "$DOMAIN_NAME" --non-interactive || true
                success "SSL certificate removed."
            fi
        else
            error "Domain name cannot be empty. Skipping Nginx/SSL cleanup."
        fi
    fi

    # 5. Clean up Firewall rules
    if command -v ufw &>/dev/null && ufw status | grep -q "active"; then
        read -p "Do you want to clean up UFW firewall rules created for this relay? (y/N): " CLEAN_UFW
        if [[ "$CLEAN_UFW" =~ ^[Yy]$ ]]; then
            # Delete direct custom port rule if we knew the port
            if [ -n "$RELAY_PORT" ]; then
                info "Removing UFW rule for port $RELAY_PORT..."
                ufw delete allow "$RELAY_PORT"/tcp || true
            fi
            
            # Offer to remove Nginx rules if they want, but warning that other sites might use them
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
    echo "All Middle Server Relay components have been completely uninstalled."
    echo "========================================================"
    echo ""
}

# Main Execution Flow - Argument Parsing
UNINSTALL_FLAG=false

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -u|--uninstall) UNINSTALL_FLAG=true ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo "Options:"
            echo "  -u, --uninstall   Directly trigger the uninstaller"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        *) error "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# If uninstall flag is passed, run directly
if [ "$UNINSTALL_FLAG" = true ]; then
    uninstall_relay
    exit 0
fi

# Otherwise, present interactive menu
while true; do
    echo -e "${CYAN}${BOLD}VPN Bot Middle Server Relay Tool${NC}"
    echo "--------------------------------------------------------"
    echo "1) Install / Update Relay Service"
    echo "2) Uninstall Relay Service"
    echo "3) Exit"
    echo "--------------------------------------------------------"
    read -p "Please select an option (1-3): " CHOICE

    case $CHOICE in
        1)
            install_relay
            break
            ;;
        2)
            uninstall_relay
            break
            ;;
        3)
            info "Exiting..."
            exit 0
            ;;
        *)
            error "Invalid option. Please choose between 1 and 3."
            echo ""
            ;;
    esac
done
