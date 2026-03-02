#!/bin/bash
# GOLIATH Web Platform — Port 80 Setup
# Run this as root: sudo bash /opt/goliath/web-platform/setup-port80.sh

set -e

echo "=== Goliath Web Platform — Port 80 Setup ==="

# Kill existing node process on port 3001 if running
echo "[1/4] Stopping existing web platform..."
pkill -f "node dist/index.js" 2>/dev/null || true
sleep 1

# Install systemd service
echo "[2/4] Installing systemd service..."
cp /opt/goliath/web-platform/goliath-web.service /etc/systemd/system/goliath-web.service
systemctl daemon-reload

# Enable and start
echo "[3/4] Starting web platform on port 80..."
systemctl enable goliath-web.service
systemctl start goliath-web.service

# Verify
echo "[4/4] Verifying..."
sleep 2
if curl -s http://localhost:80/api/health | grep -q '"ok"'; then
    echo ""
    echo "✅ SUCCESS! Goliath Web Platform is live on port 80"
    echo "   Access: http://178.156.152.148"
    echo ""
else
    echo ""
    echo "❌ Something went wrong. Check: systemctl status goliath-web"
    echo ""
    systemctl status goliath-web --no-pager
fi
