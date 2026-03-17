#!/bin/bash
# Fix firewall to allow external access to Goliath Web GUI (port 3000)
# Run as root: sudo bash /opt/goliath/scripts/fix-firewall.sh
set -euo pipefail

echo "=== Current UFW Status ==="
ufw status numbered

echo ""
echo "=== Opening port 3000/tcp (Goliath Web GUI) ==="
ufw allow 3000/tcp comment "Goliath Web GUI"

echo ""
echo "=== Confirming port 8000/tcp is open (webhook server) ==="
ufw allow 8000/tcp comment "Goliath Webhook Server"

echo ""
echo "=== Updated UFW Status ==="
ufw status numbered

echo ""
echo "=== DONE ==="
echo "Port 3000 should now be accessible at http://178.156.152.148:3000"
echo "Port 8000 should now be accessible at http://178.156.152.148:8000"
