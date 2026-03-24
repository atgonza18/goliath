#!/bin/bash
# Deploy fixed goliath-web service file and restart
# Run as root: sudo bash /opt/goliath/web-platform/deploy-web-service.sh
set -e

echo "=== Deploying Goliath Web Service (PORT=8000) ==="

# 1. Copy the fixed service file
echo "[1/4] Installing service file with PORT=8000..."
cp /opt/goliath/web-platform/goliath-web.service /etc/systemd/system/goliath-web.service
systemctl daemon-reload

# 2. Restart bot first (to release port 8000 and pick up WEBHOOK_PORT=8001)
echo "[2/4] Restarting goliath-bot (webhook moving to port 8001)..."
systemctl restart goliath-bot
sleep 5

# 3. Restart web service (will now bind to port 8000)
echo "[3/4] Restarting goliath-web..."
systemctl restart goliath-web
sleep 3

# 4. Verify
echo "[4/4] Verifying..."
if curl -s http://localhost:8000/api/health | grep -q '"ok"'; then
    echo ""
    echo "✅ SUCCESS! Goliath Web Platform is live on port 8000"
    echo "   Access: http://178.156.152.148:8000"
else
    echo ""
    echo "⚠️  Health check failed. Checking status..."
    systemctl status goliath-web --no-pager
fi

echo ""
echo "=== Service Status ==="
systemctl status goliath-web --no-pager
echo ""
systemctl status goliath-bot --no-pager | head -10
