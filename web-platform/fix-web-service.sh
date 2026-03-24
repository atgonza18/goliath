#!/bin/bash
# Fix GOLIATH Web Platform systemd service
# Run as root: sudo bash /opt/goliath/web-platform/fix-web-service.sh
set -e

echo "=== Fixing Goliath Web Service ==="

# 1. Copy fixed service file (PORT=8000)
echo "[1/5] Installing fixed service file..."
cp /opt/goliath/web-platform/goliath-web.service /etc/systemd/system/goliath-web.service
systemctl daemon-reload

# 2. Stop the manually-started node process
echo "[2/5] Stopping manually-started node process..."
pkill -f "node dist/index.js" 2>/dev/null || true
sleep 2

# 3. Enable and start via systemd
echo "[3/5] Starting web platform via systemd..."
systemctl enable goliath-web.service
systemctl restart goliath-web.service
sleep 3

# 4. Verify
echo "[4/5] Verifying..."
if curl -s http://localhost:8000/api/health | grep -q '"ok"'; then
    echo ""
    echo "✅ SUCCESS! Goliath Web Platform is live on port 8000"
    echo "   Access: http://178.156.152.148:8000"
    echo ""
else
    echo ""
    echo "❌ Something went wrong. Check: systemctl status goliath-web"
    echo ""
    systemctl status goliath-web --no-pager
fi

# 5. Show status
echo "[5/5] Service status:"
systemctl status goliath-web --no-pager
