#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# ──────────────────────────────────────────────────
# Kill ALL existing bot and stale subagent processes
# to prevent duplicate instances / double responses
# ──────────────────────────────────────────────────

echo "Killing all existing bot.main processes..."
pkill -f "python -m bot.main" 2>/dev/null || true
sleep 1

# Kill any stale claude --print subagent processes left over from previous runs
echo "Killing stale claude --print subprocesses..."
pkill -f "claude --print" 2>/dev/null || true
sleep 2

# Verify bot processes are dead — escalate to SIGKILL if needed
if pgrep -f "python -m bot.main" > /dev/null 2>&1; then
    echo "Stubborn bot processes found, sending SIGKILL..."
    pkill -9 -f "python -m bot.main" 2>/dev/null || true
    sleep 1
fi

if pgrep -f "claude --print" > /dev/null 2>&1; then
    echo "Stubborn claude processes found, sending SIGKILL..."
    pkill -9 -f "claude --print" 2>/dev/null || true
    sleep 1
fi

# Final check — abort if we still can't get a clean slate
if pgrep -f "python -m bot.main" > /dev/null 2>&1; then
    echo "ERROR: Could not kill all bot.main processes. Aborting."
    exit 1
fi

rm -f bot.pid
echo "All old processes cleared."

# Activate venv if present (Hetzner uses /opt/goliath/venv)
if [ -d "$REPO_ROOT/venv" ]; then
    source "$REPO_ROOT/venv/bin/activate"
fi

# Install dependencies (fast if already installed)
pip install -q -r requirements.txt

# Start the bot in the background
echo "Starting GOLIATH Telegram Bot..."
nohup python -m bot.main > bot.log 2>&1 &
echo $! > bot.pid
echo "Bot started with PID $(cat bot.pid)"
