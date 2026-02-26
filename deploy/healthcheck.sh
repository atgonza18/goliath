#!/usr/bin/env bash
# Health check for GOLIATH bot
# Can be called by cron or monitoring tools
# Returns 0 if healthy, 1 if unhealthy

set -euo pipefail

SERVICE="goliath-bot"
MAX_LOG_AGE_MINUTES=30
LOG_FILE="/opt/goliath/telegram-bot/bot.log"

# Check 1: Is the systemd service running?
if ! systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "UNHEALTHY: Service $SERVICE is not running"
    echo "Attempting restart..."
    systemctl restart "$SERVICE"
    exit 1
fi

# Check 2: Has the log file been updated recently?
if [ -f "$LOG_FILE" ]; then
    last_modified=$(stat -c %Y "$LOG_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    age_minutes=$(( (now - last_modified) / 60 ))

    if [ "$age_minutes" -gt "$MAX_LOG_AGE_MINUTES" ]; then
        echo "WARNING: Log file hasn't been updated in ${age_minutes} minutes"
    fi
fi

# Check 3: Are there stuck Claude CLI processes? (>15 min)
stuck_count=$(find /proc -maxdepth 2 -name cmdline -exec grep -l "claude.*--print" {} \; 2>/dev/null | while read f; do
    pid=$(echo "$f" | cut -d/ -f3)
    # Check if process is older than 15 minutes (900 seconds)
    if [ -f "/proc/$pid/stat" ]; then
        start_time=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || echo 0)
        uptime_ticks=$(awk '{print $1}' /proc/uptime 2>/dev/null | cut -d. -f1)
        clk_tck=$(getconf CLK_TCK)
        proc_age=$(( uptime_ticks - start_time / clk_tck ))
        if [ "$proc_age" -gt 900 ]; then
            echo "$pid"
        fi
    fi
done | wc -l)

if [ "$stuck_count" -gt 0 ]; then
    echo "WARNING: $stuck_count stuck Claude CLI process(es) detected (>15 min)"
    echo "Killing stuck processes..."
    pkill -f "claude.*--print" --older 15m 2>/dev/null || true
fi

echo "HEALTHY: $SERVICE is running"
exit 0
