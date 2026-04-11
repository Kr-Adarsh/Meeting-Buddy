#!/bin/bash
# Meeting Buddy AWS Deployment Script
# Run this inside /home/ubuntu/Meeting-Buddy AFTER a git pull to sync all changes magically!

echo "====================================="
echo "🚀 Meeting Buddy Cloud Deployer"
echo "====================================="

echo "[1/4] Installing / Updating Python Backend Dependencies..."
source .venv/bin/activate
pip install -r requirements.txt

echo "[2/4] Syncing Nginx Reverse Proxy & Removing Basic Auth..."
sudo cp aws_deploy/nginx.conf.template /etc/nginx/sites-available/meetingbuddy
sudo systemctl reload nginx
echo "✅ Nginx Basic Auth purged and reloaded."

echo "[3/4] Rebuilding Frontend UI (React + Vite)..."
cd frontend
npm install
npm run build
cd ..
echo "✅ Frontend injected with new Markdown UI rendering."

echo "[4/4] Restarting Python Daemon Backend..."
sudo systemctl restart meetingbuddy.service

echo "====================================="
echo "🎉 DEPLOYMENT COMPLETE! "
echo "Run 'journalctl -u meetingbuddy.service -f' to monitor the live backend logs."
echo "====================================="
