#!/usr/bin/env bash
# start-tunnels.sh — Manage Cloudflare quick tunnels for HTTPS access
# Starts two tunnels: one for the web dashboard (port 3000) and one for
# the webhook server (port 8000). Captures and logs the tunnel URLs.
#
# Usage: ./start-tunnels.sh [start|stop|status|urls]

set -euo pipefail

CLOUDFLARED="/home/goliath/cloudflared"
WEB_LOG="/tmp/cloudflared-web.log"
WEBHOOK_LOG="/tmp/cloudflared-webhook.log"
URL_FILE="/opt/goliath/data/tunnel-urls.json"
ENV_FILE="/opt/goliath/.env"

WEB_PORT=3000
WEBHOOK_PORT=8000

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

get_pid() {
    local port="$1"
    pgrep -f "cloudflared tunnel --url http://localhost:${port}" 2>/dev/null || true
}

extract_url() {
    local logfile="$1"
    local max_wait=15
    local waited=0
    while [ $waited -lt $max_wait ]; do
        local url
        url=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$logfile" 2>/dev/null | tail -1)
        if [ -n "$url" ]; then
            echo "$url"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    echo ""
    return 1
}

start_tunnel() {
    local port="$1"
    local logfile="$2"
    local label="$3"

    local existing_pid
    existing_pid=$(get_pid "$port")
    if [ -n "$existing_pid" ]; then
        log "${label} tunnel already running (PID: ${existing_pid})"
        return 0
    fi

    log "Starting ${label} tunnel on port ${port}..."
    > "$logfile"  # Clear log
    nohup "$CLOUDFLARED" tunnel --url "http://localhost:${port}" >> "$logfile" 2>&1 &
    local pid=$!
    log "${label} tunnel started (PID: ${pid})"

    local url
    url=$(extract_url "$logfile")
    if [ -n "$url" ]; then
        log "${label} tunnel URL: ${url}"
        echo "$url"
    else
        log "WARNING: Could not extract ${label} tunnel URL within timeout"
        echo ""
    fi
}

stop_tunnels() {
    log "Stopping all cloudflared tunnels..."
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    sleep 2
    log "All tunnels stopped"
}

save_urls() {
    local web_url="$1"
    local webhook_url="$2"
    mkdir -p "$(dirname "$URL_FILE")"
    cat > "$URL_FILE" <<EOF
{
  "web_dashboard": "${web_url}",
  "webhook": "${webhook_url}",
  "webhook_recall": "${webhook_url}/webhook/recall",
  "updated_at": "$(date -Iseconds)"
}
EOF
    log "URLs saved to ${URL_FILE}"
}

update_env_webhook() {
    local webhook_url="$1"
    if [ -z "$webhook_url" ]; then return; fi
    local full_url="${webhook_url}/webhook/recall"
    if grep -q "^RECALL_WEBHOOK_URL=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^RECALL_WEBHOOK_URL=.*|RECALL_WEBHOOK_URL=${full_url}|" "$ENV_FILE"
        log "Updated RECALL_WEBHOOK_URL in .env to ${full_url}"
    else
        echo "RECALL_WEBHOOK_URL=${full_url}" >> "$ENV_FILE"
        log "Added RECALL_WEBHOOK_URL to .env"
    fi
}

show_status() {
    local web_pid webhook_pid
    web_pid=$(get_pid "$WEB_PORT")
    webhook_pid=$(get_pid "$WEBHOOK_PORT")

    echo "=== Cloudflare Tunnel Status ==="
    if [ -n "$web_pid" ]; then
        echo "Web Dashboard (port ${WEB_PORT}): RUNNING (PID: ${web_pid})"
    else
        echo "Web Dashboard (port ${WEB_PORT}): STOPPED"
    fi
    if [ -n "$webhook_pid" ]; then
        echo "Webhook (port ${WEBHOOK_PORT}): RUNNING (PID: ${webhook_pid})"
    else
        echo "Webhook (port ${WEBHOOK_PORT}): STOPPED"
    fi

    if [ -f "$URL_FILE" ]; then
        echo ""
        echo "=== Saved URLs ==="
        cat "$URL_FILE"
    fi
}

show_urls() {
    if [ -f "$URL_FILE" ]; then
        cat "$URL_FILE"
    else
        echo "No saved URLs. Run 'start-tunnels.sh start' first."
    fi
}

case "${1:-start}" in
    start)
        log "Starting Cloudflare tunnels..."
        web_url=$(start_tunnel "$WEB_PORT" "$WEB_LOG" "Web Dashboard")
        webhook_url=$(start_tunnel "$WEBHOOK_PORT" "$WEBHOOK_LOG" "Webhook")
        save_urls "$web_url" "$webhook_url"
        update_env_webhook "$webhook_url"
        echo ""
        show_status
        ;;
    stop)
        stop_tunnels
        ;;
    restart)
        stop_tunnels
        sleep 2
        exec "$0" start
        ;;
    status)
        show_status
        ;;
    urls)
        show_urls
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|urls}"
        exit 1
        ;;
esac
