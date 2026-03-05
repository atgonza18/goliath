#!/usr/bin/env bash
# Health check for GOLIATH bot
# Can be called by cron or monitoring tools
# Returns 0 if healthy, 1 if unhealthy
#
# IMPORTANT: This script must NEVER kill Claude CLI processes.
# The runner.py SubagentRunner has its own 12-minute operational timeout
# and circuit breaker. External process killing causes false positives
# because grep/pkill -f matches system prompt text inside process args,
# not just actual stuck processes. 1,644 confirmed false kills before
# this was fixed on 2026-03-05.

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

# NOTE: Stuck Claude CLI process detection was REMOVED on 2026-03-05.
# Reason: pkill -f / grep against /proc/*/cmdline matches ANY process
# whose command-line arguments contain "claude.*--print" — including
# the DevOps agent, whose system prompt (passed via --system-prompt)
# contains that exact text as documentation. This caused 1,644 false
# kills of legitimate, actively-running agent processes.
#
# The runner.py already handles stuck processes correctly:
#   - OPERATIONAL_TIMEOUT (12 min) kills genuinely stuck subagents
#   - Circuit breaker backs off after 3 consecutive failures
#   - asyncio.wait_for() enforces hard deadline per subprocess
#
# DO NOT re-add process killing here. If you need zombie cleanup,
# implement it in runner.py using tracked PIDs, not pattern matching.

echo "HEALTHY: $SERVICE is running"
exit 0
