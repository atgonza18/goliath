#!/usr/bin/env bash
# =============================================================================
# Goliath App Builder — Phase 1 Infrastructure Setup
# =============================================================================
# This script installs and configures the foundation infrastructure:
#   1. Docker Engine (if not already installed)
#   2. Docker Compose v2 plugin (if not already installed)
#   3. goliath-apps Docker network
#   4. Traefik reverse proxy container
#   5. Hello World test app to validate the pipeline
#
# Prerequisites:
#   - Ubuntu 22.04+ (other distros: adjust package manager commands)
#   - Root or sudo access
#   - A domain with wildcard DNS pointed at this server
#   - /opt/goliath/infrastructure/.env filled in (copy from .env.example)
#
# Usage:
#   cd /opt/goliath/infrastructure
#   cp .env.example .env    # Edit .env with your domain + email
#   sudo bash setup-phase1.sh
#
# What this script does NOT do:
#   - Configure DNS (you must set up *.yourdomain.com → server IP)
#   - Open firewall ports (ensure ports 80 + 443 are accessible)
# =============================================================================

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# ─── Logging ──────────────────────────────────────────────────────────────────
log()    { echo -e "${BLUE}[GOLIATH]${NC} $1"; }
success(){ echo -e "${GREEN}[  OK  ]${NC} $1"; }
warn()   { echo -e "${YELLOW}[ WARN ]${NC} $1"; }
error()  { echo -e "${RED}[ERROR ]${NC} $1"; }
header() { echo -e "\n${CYAN}${BOLD}━━━ $1 ━━━${NC}\n"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

# ─── Pre-flight checks ───────────────────────────────────────────────────────
header "Phase 1: Pre-flight Checks"

# Must run as root or with sudo
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use: sudo bash setup-phase1.sh)"
    exit 1
fi

# Check for .env file
if [[ ! -f "$ENV_FILE" ]]; then
    error ".env file not found at $ENV_FILE"
    echo ""
    echo "  Create it from the example:"
    echo "    cp ${SCRIPT_DIR}/.env.example ${ENV_FILE}"
    echo "    nano ${ENV_FILE}"
    echo ""
    exit 1
fi

# Source .env and validate required vars
set -a
source "$ENV_FILE"
set +a

if [[ -z "${GOLIATH_DOMAIN:-}" ]]; then
    error "GOLIATH_DOMAIN is not set in .env"
    exit 1
fi

if [[ -z "${ACME_EMAIL:-}" ]]; then
    error "ACME_EMAIL is not set in .env"
    exit 1
fi

success "Domain: ${GOLIATH_DOMAIN}"
success "ACME email: ${ACME_EMAIL}"
log "Server IP: $(hostname -I | awk '{print $1}')"

# ─── Step 1: Install Docker Engine ───────────────────────────────────────────
header "Step 1: Docker Engine"

if command -v docker &> /dev/null; then
    DOCKER_VERSION=$(docker --version 2>/dev/null || echo "unknown")
    success "Docker already installed: ${DOCKER_VERSION}"
else
    log "Installing Docker Engine..."

    # Remove old versions
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

    # Install prerequisites
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg

    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
        chmod a+r /etc/apt/keyrings/docker.gpg
    fi

    # Add Docker apt repo
    ARCH=$(dpkg --print-architecture)
    CODENAME=$(. /etc/os-release && echo "$VERSION_CODENAME")
    echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list

    # Install Docker
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Start and enable Docker
    systemctl start docker
    systemctl enable docker

    # Add goliath user to docker group (run without sudo in future)
    if id "goliath" &>/dev/null; then
        usermod -aG docker goliath
        log "Added 'goliath' user to docker group (re-login required for non-sudo docker)"
    fi

    success "Docker Engine installed: $(docker --version)"
fi

# ─── Step 2: Verify Docker Compose ───────────────────────────────────────────
header "Step 2: Docker Compose"

if docker compose version &> /dev/null; then
    success "Docker Compose available: $(docker compose version)"
else
    error "Docker Compose plugin not found. It should have been installed with Docker."
    error "Try: apt-get install -y docker-compose-plugin"
    exit 1
fi

# ─── Step 3: Create Docker Network ───────────────────────────────────────────
header "Step 3: Docker Network"

if docker network inspect goliath-apps &> /dev/null; then
    success "Network 'goliath-apps' already exists"
else
    docker network create goliath-apps
    success "Created Docker network 'goliath-apps'"
fi

# ─── Step 4: Check Firewall / Port Availability ──────────────────────────────
header "Step 4: Port Availability"

check_port() {
    local PORT=$1
    if ss -tlnp | grep -q ":${PORT} " 2>/dev/null; then
        warn "Port ${PORT} is already in use:"
        ss -tlnp | grep ":${PORT} " | head -3
        return 1
    else
        success "Port ${PORT} is available"
        return 0
    fi
}

PORT_80_OK=true
PORT_443_OK=true

check_port 80  || PORT_80_OK=false
check_port 443 || PORT_443_OK=false

if [[ "$PORT_80_OK" == false || "$PORT_443_OK" == false ]]; then
    warn "Traefik needs ports 80 and 443. If another service is using them,"
    warn "you'll need to stop it first or configure Traefik on different ports."
    echo ""
    read -p "Continue anyway? [y/N] " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        error "Aborted by user."
        exit 1
    fi
fi

# Check UFW firewall
if command -v ufw &> /dev/null; then
    UFW_STATUS=$(ufw status 2>/dev/null || echo "inactive")
    if echo "$UFW_STATUS" | grep -q "active"; then
        log "UFW firewall is active. Ensuring ports 80 and 443 are allowed..."
        ufw allow 80/tcp   2>/dev/null || true
        ufw allow 443/tcp  2>/dev/null || true
        success "Firewall rules added for ports 80 and 443"
    else
        success "UFW firewall is inactive (no rules needed)"
    fi
fi

# ─── Step 5: Start Traefik ───────────────────────────────────────────────────
header "Step 5: Traefik Reverse Proxy"

cd "$SCRIPT_DIR"

# Check if Traefik is already running
if docker ps --format '{{.Names}}' | grep -q '^goliath-traefik$'; then
    success "Traefik is already running"
    docker ps --filter name=goliath-traefik --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
else
    log "Starting Traefik..."
    docker compose -f docker-compose.traefik.yml --env-file "$ENV_FILE" up -d

    # Wait for Traefik to be healthy
    log "Waiting for Traefik to start..."
    for i in $(seq 1 30); do
        if docker ps --filter name=goliath-traefik --filter status=running --format '{{.Names}}' | grep -q 'goliath-traefik'; then
            break
        fi
        sleep 1
    done

    if docker ps --filter name=goliath-traefik --filter status=running --format '{{.Names}}' | grep -q 'goliath-traefik'; then
        success "Traefik is running"
        docker ps --filter name=goliath-traefik --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    else
        error "Traefik failed to start. Check logs:"
        echo "  docker logs goliath-traefik"
        exit 1
    fi
fi

# ─── Step 6: Deploy Hello World Test App ─────────────────────────────────────
header "Step 6: Hello World Test App"

if docker ps --format '{{.Names}}' | grep -q '^goliath-hello-world$'; then
    success "Hello World app is already running"
else
    log "Deploying Hello World test app..."
    docker compose -f docker-compose.hello-world.yml --env-file "$ENV_FILE" up -d

    # Wait for it to start
    sleep 3

    if docker ps --filter name=goliath-hello-world --filter status=running --format '{{.Names}}' | grep -q 'goliath-hello-world'; then
        success "Hello World app is running"
    else
        error "Hello World app failed to start. Check logs:"
        echo "  docker logs goliath-hello-world"
        exit 1
    fi
fi

# ─── Step 7: DNS Check ───────────────────────────────────────────────────────
header "Step 7: DNS Verification"

SERVER_IP=$(hostname -I | awk '{print $1}')

log "Checking if hello.${GOLIATH_DOMAIN} resolves to this server..."

if command -v dig &> /dev/null; then
    RESOLVED_IP=$(dig +short "hello.${GOLIATH_DOMAIN}" 2>/dev/null | tail -1)
elif command -v host &> /dev/null; then
    RESOLVED_IP=$(host "hello.${GOLIATH_DOMAIN}" 2>/dev/null | awk '/has address/ {print $4}' | tail -1)
elif command -v nslookup &> /dev/null; then
    RESOLVED_IP=$(nslookup "hello.${GOLIATH_DOMAIN}" 2>/dev/null | awk '/^Address: / {print $2}' | tail -1)
else
    RESOLVED_IP=""
    warn "No DNS lookup tool found (dig/host/nslookup). Skipping DNS check."
fi

if [[ -n "$RESOLVED_IP" ]]; then
    if [[ "$RESOLVED_IP" == "$SERVER_IP" ]]; then
        success "DNS OK: hello.${GOLIATH_DOMAIN} → ${RESOLVED_IP}"
    else
        warn "DNS mismatch: hello.${GOLIATH_DOMAIN} → ${RESOLVED_IP} (expected ${SERVER_IP})"
        warn "Make sure *.${GOLIATH_DOMAIN} has an A record pointing to ${SERVER_IP}"
    fi
else
    warn "Could not resolve hello.${GOLIATH_DOMAIN}"
    warn "Set up wildcard DNS: *.${GOLIATH_DOMAIN} → ${SERVER_IP}"
fi

# ─── Step 8: End-to-End Test ─────────────────────────────────────────────────
header "Step 8: End-to-End Verification"

log "Testing HTTPS connectivity to hello.${GOLIATH_DOMAIN}..."

# Give Let's Encrypt a moment to provision the cert
sleep 5

# Try HTTPS first, then HTTP as fallback
HTTP_CODE=$(curl -sSo /dev/null -w "%{http_code}" --max-time 15 "https://hello.${GOLIATH_DOMAIN}" 2>/dev/null || echo "000")

if [[ "$HTTP_CODE" == "200" ]]; then
    success "HTTPS test PASSED! Got HTTP 200 from https://hello.${GOLIATH_DOMAIN}"
elif [[ "$HTTP_CODE" == "000" ]]; then
    warn "Could not connect via HTTPS. This is normal if DNS isn't configured yet."
    warn "Once DNS is set up, test with: curl -I https://hello.${GOLIATH_DOMAIN}"

    # Try reaching Traefik directly
    log "Testing Traefik locally on port 80..."
    LOCAL_CODE=$(curl -sSo /dev/null -w "%{http_code}" --max-time 5 -H "Host: hello.${GOLIATH_DOMAIN}" "http://localhost" 2>/dev/null || echo "000")
    if [[ "$LOCAL_CODE" == "301" || "$LOCAL_CODE" == "200" ]]; then
        success "Traefik is responding locally (HTTP ${LOCAL_CODE}). DNS is the remaining step."
    else
        warn "Traefik not responding locally (HTTP ${LOCAL_CODE}). Check: docker logs goliath-traefik"
    fi
else
    warn "Got HTTP ${HTTP_CODE} from hello.${GOLIATH_DOMAIN}. SSL may still be provisioning."
    warn "Wait 1-2 minutes and try: curl -I https://hello.${GOLIATH_DOMAIN}"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
header "Phase 1 Setup Summary"

echo -e "${BOLD}Infrastructure Status:${NC}"
echo ""

# Docker
DOCKER_STATUS=$(docker --version 2>/dev/null && echo "INSTALLED" || echo "MISSING")
echo -e "  Docker Engine ........... ${GREEN}${DOCKER_STATUS}${NC}"

# Docker Compose
COMPOSE_STATUS=$(docker compose version 2>/dev/null && echo "INSTALLED" || echo "MISSING")
echo -e "  Docker Compose .......... ${GREEN}${COMPOSE_STATUS}${NC}"

# Network
NET_STATUS=$(docker network inspect goliath-apps &>/dev/null && echo "CREATED" || echo "MISSING")
echo -e "  goliath-apps network .... ${GREEN}${NET_STATUS}${NC}"

# Traefik
TRAEFIK_STATUS=$(docker ps --filter name=goliath-traefik --filter status=running -q 2>/dev/null)
if [[ -n "$TRAEFIK_STATUS" ]]; then
    echo -e "  Traefik proxy ........... ${GREEN}RUNNING${NC}"
else
    echo -e "  Traefik proxy ........... ${RED}NOT RUNNING${NC}"
fi

# Hello World
HELLO_STATUS=$(docker ps --filter name=goliath-hello-world --filter status=running -q 2>/dev/null)
if [[ -n "$HELLO_STATUS" ]]; then
    echo -e "  Hello World app ......... ${GREEN}RUNNING${NC}"
else
    echo -e "  Hello World app ......... ${RED}NOT RUNNING${NC}"
fi

echo ""
echo -e "${BOLD}URLs:${NC}"
echo -e "  Test app:    https://hello.${GOLIATH_DOMAIN}"
echo -e "  Dashboard:   https://traefik.${GOLIATH_DOMAIN}  (if auth configured)"
echo ""
echo -e "${BOLD}DNS Required:${NC}"
echo -e "  Add a wildcard A record:  *.${GOLIATH_DOMAIN} → ${SERVER_IP}"
echo -e "  Or individual A records:  hello.${GOLIATH_DOMAIN} → ${SERVER_IP}"
echo ""
echo -e "${BOLD}Next Steps:${NC}"
echo -e "  1. Set up DNS if not done yet"
echo -e "  2. Verify: curl -I https://hello.${GOLIATH_DOMAIN}"
echo -e "  3. Phase 2: Preview panel + Ship pipeline in the Goliath GUI"
echo ""

# ─── Write status file ────────────────────────────────────────────────────────
STATUS_FILE="${SCRIPT_DIR}/phase1-status.json"
cat > "$STATUS_FILE" << STATUSEOF
{
  "phase": 1,
  "setup_completed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "domain": "${GOLIATH_DOMAIN}",
  "server_ip": "${SERVER_IP}",
  "docker_installed": true,
  "compose_installed": true,
  "traefik_running": $(docker ps --filter name=goliath-traefik --filter status=running -q &>/dev/null && echo "true" || echo "false"),
  "hello_world_running": $(docker ps --filter name=goliath-hello-world --filter status=running -q &>/dev/null && echo "true" || echo "false"),
  "hello_world_url": "https://hello.${GOLIATH_DOMAIN}",
  "traefik_dashboard_url": "https://traefik.${GOLIATH_DOMAIN}"
}
STATUSEOF

success "Status written to ${STATUS_FILE}"
log "Phase 1 setup complete!"
