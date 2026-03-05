#!/usr/bin/env bash
# One-shot script to apply TCP keepalive settings to the kernel.
# Must be run as root: sudo bash /opt/goliath/deploy/apply-keepalive.sh
#
# This copies the keepalive config to /etc/sysctl.d/ and applies it immediately.
# Settings persist across reboots via the sysctl.d config file.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

CONF_SRC="/opt/goliath/deploy/99-goliath-keepalive.conf"
CONF_DST="/etc/sysctl.d/99-goliath-keepalive.conf"

if [ ! -f "$CONF_SRC" ]; then
    echo "ERROR: Source config not found: $CONF_SRC"
    exit 1
fi

echo "Installing TCP keepalive config..."
cp "$CONF_SRC" "$CONF_DST"
chmod 644 "$CONF_DST"

echo "Applying settings to running kernel..."
sysctl -p "$CONF_DST"

echo ""
echo "Verifying applied values:"
sysctl net.ipv4.tcp_keepalive_time net.ipv4.tcp_keepalive_intvl net.ipv4.tcp_keepalive_probes

echo ""
echo "SUCCESS: TCP keepalive settings applied and will persist across reboots."
