#!/usr/bin/env bash
#
# GOLIATH — Hetzner VPS Setup Script
#
# Run this on a fresh Ubuntu 22.04+ VPS:
#   curl -sSL https://raw.githubusercontent.com/atgonza18/goliath/main/deploy/setup-hetzner.sh | bash
#
# Or clone first and run locally:
#   git clone https://github.com/atgonza18/goliath.git /opt/goliath
#   bash /opt/goliath/deploy/setup-hetzner.sh
#
# Prerequisites:
#   - Ubuntu 22.04 LTS or newer
#   - Root or sudo access
#   - Internet access
#   - Your TELEGRAM_BOT_TOKEN ready
#
set -euo pipefail

# ──────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────
GOLIATH_ROOT="/opt/goliath"
GOLIATH_USER="goliath"
GOLIATH_REPO="https://github.com/atgonza18/goliath.git"
PYTHON_MIN="3.10"
NODE_MIN="18"  # Required for Claude CLI

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log()   { echo -e "${GREEN}[GOLIATH]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ──────────────────────────────────────────
# Pre-flight checks
# ──────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root (or with sudo)."
    exit 1
fi

log "Starting GOLIATH Hetzner setup..."
log "Target directory: $GOLIATH_ROOT"

# ──────────────────────────────────────────
# Step 1: System packages
# ──────────────────────────────────────────
log "Step 1/9: Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    git curl wget unzip \
    sqlite3 \
    logrotate \
    > /dev/null

# Check Python version
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
log "Python version: $PYTHON_VER"

# ──────────────────────────────────────────
# Step 2: Install Node.js (for Claude CLI)
# ──────────────────────────────────────────
log "Step 2/9: Installing Node.js (required for Claude CLI)..."
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
    apt-get install -y -qq nodejs > /dev/null
fi
NODE_VER=$(node --version)
log "Node.js version: $NODE_VER"

# ──────────────────────────────────────────
# Step 3: Install Claude CLI
# ──────────────────────────────────────────
log "Step 3/9: Installing Claude CLI..."
if ! command -v claude &>/dev/null; then
    npm install -g @anthropic-ai/claude-code > /dev/null 2>&1
    log "Claude CLI installed: $(claude --version 2>/dev/null || echo 'installed')"
else
    log "Claude CLI already installed: $(claude --version 2>/dev/null || echo 'present')"
fi

echo ""
warn "═══════════════════════════════════════════════════════════"
warn "  IMPORTANT: Claude CLI must be authenticated."
warn "  After this script completes, run as the goliath user:"
warn "    sudo -u goliath claude auth login"
warn "═══════════════════════════════════════════════════════════"
echo ""

# ──────────────────────────────────────────
# Step 4: Create goliath user
# ──────────────────────────────────────────
log "Step 4/9: Creating goliath system user..."
if ! id "$GOLIATH_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$GOLIATH_USER"
    log "Created user: $GOLIATH_USER"
else
    log "User $GOLIATH_USER already exists"
fi

# ──────────────────────────────────────────
# Step 5: Clone or update repo
# ──────────────────────────────────────────
log "Step 5/9: Setting up repository..."
if [ -d "$GOLIATH_ROOT/.git" ]; then
    log "Repository exists, pulling latest..."
    cd "$GOLIATH_ROOT"
    git pull --ff-only || warn "Could not pull — may have local changes"
else
    log "Cloning repository..."
    git clone "$GOLIATH_REPO" "$GOLIATH_ROOT"
fi

chown -R "$GOLIATH_USER:$GOLIATH_USER" "$GOLIATH_ROOT"

# ──────────────────────────────────────────
# Step 6: Python virtual environment + dependencies
# ──────────────────────────────────────────
log "Step 6/9: Setting up Python virtual environment..."
sudo -u "$GOLIATH_USER" bash -c "
    cd $GOLIATH_ROOT
    python3 -m venv venv
    source venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -r telegram-bot/requirements.txt
"
log "Python dependencies installed"

# ──────────────────────────────────────────
# Step 7: Environment file
# ──────────────────────────────────────────
log "Step 7/9: Configuring environment..."
ENV_FILE="$GOLIATH_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# GOLIATH Environment Configuration
# Required:
TELEGRAM_BOT_TOKEN=your-token-here

# Optional: Restrict bot access to specific Telegram chat IDs (comma-separated)
# ALLOWED_CHAT_IDS=123456789,987654321

# Optional: Chat ID for scheduled report delivery
# REPORT_CHAT_ID=123456789

# Optional: Webhook settings
# WEBHOOK_PORT=8000
# WEBHOOK_AUTH_TOKEN=
# TEAMS_INCOMING_WEBHOOK_URL=
ENVEOF
    chown "$GOLIATH_USER:$GOLIATH_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    echo ""
    warn "═══════════════════════════════════════════════════════════"
    warn "  IMPORTANT: Edit $ENV_FILE and set your TELEGRAM_BOT_TOKEN"
    warn "    nano $ENV_FILE"
    warn "═══════════════════════════════════════════════════════════"
    echo ""
else
    log ".env file already exists"
fi

# ──────────────────────────────────────────
# Step 8: Systemd service + log rotation + cron + health check
# ──────────────────────────────────────────
log "Step 8/9: Installing systemd service..."

# Systemd service
cp "$GOLIATH_ROOT/deploy/goliath-bot.service" /etc/systemd/system/goliath-bot.service
systemctl daemon-reload
systemctl enable goliath-bot
log "Systemd service installed and enabled"

# Log rotation
cp "$GOLIATH_ROOT/deploy/goliath-logrotate.conf" /etc/logrotate.d/goliath
log "Log rotation configured"

# Create data and reports directories
sudo -u "$GOLIATH_USER" mkdir -p "$GOLIATH_ROOT/telegram-bot/data"
sudo -u "$GOLIATH_USER" mkdir -p "$GOLIATH_ROOT/cron-jobs/reports"
sudo -u "$GOLIATH_USER" mkdir -p "$GOLIATH_ROOT/.secrets"

# Health check cron (every 10 minutes — service-alive check only, no process killing)
# NOTE: healthcheck.sh only checks if systemd service is running + log freshness.
# Stuck Claude CLI processes are handled by runner.py's built-in 12-min timeout.
# DO NOT re-add process killing — see healthcheck.sh comments for full history.
HEALTHCHECK_CRON="*/10 * * * * $GOLIATH_ROOT/deploy/healthcheck.sh >> /var/log/goliath-health.log 2>&1"
(crontab -l 2>/dev/null | grep -v "healthcheck.sh"; echo "$HEALTHCHECK_CRON") | crontab -
log "Health check cron installed (every 10 minutes — service-alive only)"

# TCP keepalive settings (prevents Hetzner NAT from dropping idle connections)
if [ -f "$GOLIATH_ROOT/deploy/99-goliath-keepalive.conf" ]; then
    cp "$GOLIATH_ROOT/deploy/99-goliath-keepalive.conf" /etc/sysctl.d/99-goliath-keepalive.conf
    sysctl -p /etc/sysctl.d/99-goliath-keepalive.conf
    log "TCP keepalive settings applied (60s idle, 10s interval, 6 probes)"
fi

# Set timezone to US Central
timedatectl set-timezone America/Chicago 2>/dev/null || warn "Could not set timezone (may need manual setup)"
log "Timezone set to America/Chicago (US Central)"

# ──────────────────────────────────────────
# Step 9: Firewall (UFW)
# ──────────────────────────────────────────
log "Step 9/9: Configuring firewall (UFW)..."

# Ensure UFW is installed
apt-get install -y -qq ufw > /dev/null 2>&1

# Allow SSH first (critical — don't lock yourself out)
ufw allow 22/tcp comment "SSH"

# Allow Goliath Web GUI
ufw allow 3000/tcp comment "Goliath Web GUI"

# Allow Goliath Webhook Server
ufw allow 8000/tcp comment "Goliath Webhook Server"

# Enable UFW if not already active (--force skips the interactive prompt)
if ! ufw status | grep -q "Status: active"; then
    ufw --force enable
fi

log "Firewall configured: SSH (22), Web GUI (3000), Webhook (8000) allowed"

# ──────────────────────────────────────────
# Done
# ──────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════"
log "GOLIATH setup complete!"
echo "══════════════════════════════════════════════════════════════"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Edit the environment file:"
echo "     nano $ENV_FILE"
echo "     (Set TELEGRAM_BOT_TOKEN, ALLOWED_CHAT_IDS, REPORT_CHAT_ID)"
echo ""
echo "  2. Authenticate Claude CLI as the goliath user:"
echo "     sudo -u $GOLIATH_USER claude auth login"
echo ""
echo "  3. Start the bot:"
echo "     systemctl start goliath-bot"
echo ""
echo "  4. Check status:"
echo "     systemctl status goliath-bot"
echo "     journalctl -u goliath-bot -f"
echo ""
echo "  5. (Optional) Transfer project data from a previous environment:"
echo "     rsync -avz source-host:/path/to/projects/ $GOLIATH_ROOT/projects/"
echo "     rsync -avz source-host:/path/to/dsc-constraints-production-reports/ $GOLIATH_ROOT/dsc-constraints-production-reports/"
echo ""
echo "  6. (Optional) Transfer memory database from a previous environment:"
echo "     rsync -avz source-host:/path/to/telegram-bot/data/memory.db $GOLIATH_ROOT/telegram-bot/data/"
echo ""
echo "  7. (Optional) Install cron jobs for scheduled reports:"
echo "     Edit $GOLIATH_ROOT/cron-jobs/crontab.txt and set GOLIATH_ROOT"
echo "     sudo -u $GOLIATH_USER crontab $GOLIATH_ROOT/cron-jobs/crontab.txt"
echo ""
echo "══════════════════════════════════════════════════════════════"
