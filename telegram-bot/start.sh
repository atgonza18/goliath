#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# Kill existing bot process if running (prevent duplicates)
if [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null; then
    echo "Stopping existing bot (PID $(cat bot.pid))..."
    kill "$(cat bot.pid)" 2>/dev/null || true
    sleep 2
fi

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
