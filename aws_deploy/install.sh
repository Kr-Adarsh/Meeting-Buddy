#!/bin/bash
# Meeting Buddy - AWS EC2 Initialization Script
# Run with: sudo ./install.sh

set -e

# 1. SETUP 4GB MEMORY SWAP (Critical for t3.micro RAM limits)
if [ ! -f /swapfile ]; then
    echo "[1/7] Allocating 4GB Swap Space..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
else
    echo "[1/7] Swap space already exists. Skipping."
fi

# 2. INSTALL SYSTEM DEPENDENCIES
echo "[2/7] Installing Linux Headless Dependencies..."
sudo apt-get update
sudo apt-get install -y nginx apache2-utils xvfb pulseaudio dbus-x11 ffmpeg python3-pip python3-venv wget unzip curl

# Install Google Chrome natively
if ! command -v google-chrome &> /dev/null; then
    echo "Installing Google Chrome..."
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get install -y ./google-chrome-stable_current_amd64.deb
    rm google-chrome-stable_current_amd64.deb
fi

# Install Node.js securely (Required for Vite React)
if ! command -v node &> /dev/null; then
    echo "Installing Node.js 20..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y nodejs
fi

# 3. NGINX CONFIGURATION
echo "[3/7] Linking Reverse Proxy Nginx Config..."
sudo cp nginx.conf.template /etc/nginx/sites-available/meetingbuddy
sudo ln -sf /etc/nginx/sites-available/meetingbuddy /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl restart nginx

# 4. PYTHON VIRTUAL ENVIRONMENT & PIP
echo "[4/7] Building Python Workspace..."
cd ..
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install google-genai

# 5. FRONTEND STATIC BUILD
echo "[5/7] Compiling React Markdown Native UI..."
cd frontend
npm install
npm run build
cd ..

# 6. SYSTEMD SERVICE REGISTRATION
echo "[6/7] Establishing systemd Background Daemon..."
cd aws_deploy
sudo cp meetingbuddy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable meetingbuddy.service
sudo systemctl restart meetingbuddy.service

echo "================================================="
echo "🎉 FULL DEPLOYMENT COMPLETE!"
echo "The Selenium Bot, React UI, and SQLite Backend are now live on your server."
echo "Make sure your .env variables are properly seated!"
echo "Monitor logs with: journalctl -u meetingbuddy.service -f"
echo "================================================="
