#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

# ──────────────────────────────────────────────────
# Kill existing bot and its child subagent processes
# to prevent duplicate instances / double responses
#
# IMPORTANT: We kill the bot FIRST, then kill only its
# orphaned child claude processes (ppid=1 after parent dies).
# We do NOT use broad "pkill -f claude --print" because that
# pattern matches ANY process whose args contain that text,
# including agents whose system prompts mention it.
# ──────────────────────────────────────────────────

# Step 1: Record the bot's PID (if running) so we can find its children
OLD_BOT_PID=$(cat bot.pid 2>/dev/null || echo "")
OLD_BOT_PIDS=$(pgrep -f "python -m bot.main" 2>/dev/null || echo "")

# Step 2: Kill the bot process(es)
echo "Killing all existing bot.main processes..."
pkill -f "python -m bot.main" 2>/dev/null || true
sleep 1

# Step 3: Kill orphaned claude subprocesses that were children of the old bot.
# After the bot dies, its child claude processes become children of init (ppid=1).
# We find claude processes with ppid=1 that have "--print" AND "--system-prompt"
# in their actual command — these are subagent invocations, not other claude sessions.
echo "Killing orphaned claude subagent processes..."
for pid in $(pgrep -x "claude" 2>/dev/null || true); do
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ "$ppid" = "1" ]; then
        # Double-check it's a subagent (has --print in actual command)
        cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
        if echo "$cmdline" | grep -q -- "--print"; then
            echo "  Killing orphaned claude subagent PID $pid"
            kill "$pid" 2>/dev/null || true
        fi
    fi
done
sleep 2

# Step 4: Escalate to SIGKILL for stubborn bot processes
if pgrep -f "python -m bot.main" > /dev/null 2>&1; then
    echo "Stubborn bot processes found, sending SIGKILL..."
    pkill -9 -f "python -m bot.main" 2>/dev/null || true
    sleep 1
fi

# Kill any remaining orphaned claude subagents (SIGKILL)
for pid in $(pgrep -x "claude" 2>/dev/null || true); do
    ppid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    if [ "$ppid" = "1" ]; then
        cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || echo "")
        if echo "$cmdline" | grep -q -- "--print"; then
            echo "  SIGKILL orphaned claude subagent PID $pid"
            kill -9 "$pid" 2>/dev/null || true
        fi
    fi
done

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
