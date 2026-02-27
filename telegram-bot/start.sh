#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# Kill ALL existing bot processes (prevent duplicates / double responses)
echo "Killing all existing bot.main processes..."
pkill -f "python -m bot.main" 2>/dev/null || true
pkill -f "python -m bot.main" 2>/dev/null || true
sleep 2
# Verify they're dead
if pgrep -f "python -m bot.main" > /dev/null 2>&1; then
    echo "Stubborn processes found, sending SIGKILL..."
    pkill -9 -f "python -m bot.main" 2>/dev/null || true
    sleep 1
fi
rm -f bot.pid

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
