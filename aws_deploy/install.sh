#!/bin/bash
# Meeting Buddy - AWS EC2 Initialization Script
# Run with: sudo ./install.sh

set -e

echo "=== Meeting Buddy Cloud Deployment ==="

# 1. SETUP 4GB MEMORY SWAP (Critical for t3.micro RAM limits)
if [ ! -f /swapfile ]; then
    echo "[1/6] Allocating 4GB Swap Space..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
    echo "[1/6] Swap space already exists. Skipping."
fi

# 2. INSTALL SYSTEM DEPENDENCIES
echo "[2/6] Installing Linux Headless Dependencies..."
sudo apt-get update
sudo apt-get install -y nginx apache2-utils xvfb pulseaudio dbus-x11 ffmpeg python3-pip python3-venv wget unzip curl

# Install Google Chrome natively
if ! command -v google-chrome &> /dev/null; then
    echo "Installing Google Chrome..."
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get install -y ./google-chrome-stable_current_amd64.deb
    rm google-chrome-stable_current_amd64.deb
fi

# 3. SET UP BASIC AUTHENTICATION
echo "[3/6] Setting up Secure Nginx Access..."
read -p "Enter a Username for Web Dashboard: " dash_user
sudo htpasswd -c /etc/nginx/.htpasswd "$dash_user"

# 4. NGINX CONFIGURATION
echo "[4/6] Linking Reverse Proxy Nginx Config..."
sudo cp nginx.conf.template /etc/nginx/sites-available/meetingbuddy
sudo ln -sf /etc/nginx/sites-available/meetingbuddy /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

# 5. PYTHON VIRTUAL ENVIRONMENT & PIP
echo "[5/6] Building Python Workspace..."
cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install google-genai
cd aws_deploy

# 6. SYSTEMD SERVICE REGISTRATION
echo "[6/6] Establishing systemd Background Daemon..."
sudo cp meetingbuddy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meetingbuddy.service
sudo systemctl restart meetingbuddy.service

echo "=========================================="
echo "DEPLOYMENT COMPLETE!"
echo "The Selenium Bot is now live on this server's Public IPv4 Address hidden entirely behind Nginx HTTP Basic Auth!"
echo "Check backend status with: sudo systemctl status meetingbuddy"
echo "=========================================="
