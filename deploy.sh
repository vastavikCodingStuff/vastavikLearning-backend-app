#!/usr/bin/env bash
set -e

# ==============================================================================
# Vastavik Learning Platform - Zero-Downtime VPS Deployment & Hardening Script
# Target: Ubuntu 22.04 / 24.04 LTS (2 vCPU, 2GB - 4GB RAM)
# ==============================================================================

echo "=========================================================="
echo "🚀 Starting Vastavik Backend Deployment & Hardening"
echo "=========================================================="

APP_DIR="/var/www/vastavik"
SERVICE_NAME="vastavik-backend"

# 1. Update and install core system utilities
echo "📦 Installing system packages..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv nginx ufw fail2ban curl git

# 2. Hardened UFW Firewall Configuration
echo "🛡️  Hardening Linux Firewall (UFW)..."
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp comment 'SSH'
sudo ufw allow 80/tcp comment 'HTTP (Certbot & Redirection)'
sudo ufw allow 443/tcp comment 'HTTPS & WebSockets'
sudo ufw --force enable

# 3. Fail2ban DDoS & Brute-force Mitigation
echo "🔒 Configuring Fail2ban..."
sudo tee /etc/fail2ban/jail.local > /dev/null << 'EOF'
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = 22

[nginx-req-limit]
enabled = true
filter = nginx-req-limit
action = iptables-multiport[name=ReqLimit, port="http,https", protocol=tcp]
logpath = /var/log/nginx/error.log
findtime = 600
maxretry = 10
bantime = 7200
EOF

sudo systemctl restart fail2ban || true

# 4. Prepare Application Directory
echo "📁 Syncing repository files to $APP_DIR..."
sudo mkdir -p $APP_DIR/uploads
sudo chown -R www-data:www-data $APP_DIR

if [ ! -d "$APP_DIR/venv" ]; then
    echo "🐍 Creating Python virtual environment..."
    sudo -u www-data python3 -m venv $APP_DIR/venv
fi

# Copy project files if running from within the repository
if [ -f "requirements.txt" ]; then
    sudo cp -r . $APP_DIR/
    sudo chown -R www-data:www-data $APP_DIR
fi

# 5. Install Dependencies
echo "⚡ Installing Python dependencies..."
sudo -u www-data $APP_DIR/venv/bin/pip install --upgrade pip
sudo -u www-data $APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt

# Create .env if not exists
if [ ! -f "$APP_DIR/.env" ] && [ -f "$APP_DIR/.env.example" ]; then
    echo "⚙️  Initializing default .env from template..."
    sudo -u www-data cp $APP_DIR/.env.example $APP_DIR/.env
fi

# 6. Configure NGINX Reverse Proxy
echo "🌐 Configuring NGINX reverse proxy..."
if [ -f "$APP_DIR/nginx/vastavik.conf" ]; then
    sudo cp $APP_DIR/nginx/vastavik.conf /etc/nginx/sites-available/vastavik
    sudo ln -sf /etc/nginx/sites-available/vastavik /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo nginx -t
    sudo systemctl reload nginx
fi

# 7. Configure and restart Systemd Service
echo "🔄 Setting up Systemd daemon..."
if [ -f "$APP_DIR/systemd/vastavik-backend.service" ]; then
    sudo cp $APP_DIR/systemd/vastavik-backend.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable $SERVICE_NAME
    sudo systemctl restart $SERVICE_NAME
fi

echo "=========================================================="
echo "✅ Vastavik Backend successfully deployed!"
echo "Status check: sudo systemctl status $SERVICE_NAME"
echo "Logs:         sudo journalctl -u $SERVICE_NAME -f"
echo "=========================================================="
