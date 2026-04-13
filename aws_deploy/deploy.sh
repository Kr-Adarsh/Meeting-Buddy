#!/bin/bash
# Meeting Buddy AWS Deployment Script
# Run this inside /home/ubuntu/Meeting-Buddy AFTER a git pull to sync all changes magically!

echo "====================================="
echo "🚀 Meeting Buddy Cloud Deployer"
echo "====================================="

echo "[1/5] Installing / Updating Python Backend Dependencies..."
source .venv/bin/activate
pip install -r requirements.txt

echo "[2/5] Syncing Nginx Reverse Proxy..."
sudo cp aws_deploy/nginx.conf.template /etc/nginx/sites-available/meetingbuddy
sudo systemctl reload nginx
echo "✅ Nginx config updated."

echo "[3/5] Re-applying SSL Certificate..."
sudo certbot --nginx -d meetingbuddy.duckdns.org --non-interactive --reinstall
sudo systemctl reload nginx
echo "✅ HTTPS SSL re-applied."

echo "[4/5] Rebuilding Frontend UI (React + Vite)..."
cd frontend
npm install
npm run build
cd ..
echo "✅ Frontend injected with new Markdown UI rendering."

echo "[5/5] Restarting Python Daemon Backend..."
sudo systemctl restart meetingbuddy.service

echo "====================================="
echo "🎉 DEPLOYMENT COMPLETE! "
echo "Run 'journalctl -u meetingbuddy.service -f' to monitor the live backend logs."
echo "====================================="
