# App Builder Platform — Phase 1 Setup Guide

**Status:** Phase 1 — Foundation Infrastructure
**Created:** 2026-03-07
**PRD:** /opt/goliath/docs/prd-app-builder-platform.md

---

## What Phase 1 Does

Phase 1 installs and configures the foundation infrastructure that all future app deployments depend on:

| Component | Purpose |
|-----------|---------|
| **Docker Engine** | Container runtime for isolated app deployments |
| **Docker Compose v2** | Multi-container orchestration (app + database per project) |
| **goliath-apps network** | Shared Docker network for inter-container communication |
| **Traefik v3** | Reverse proxy with auto-SSL and wildcard subdomain routing |
| **Hello World test app** | Validates the entire pipeline end-to-end |

After Phase 1, any new Docker container with the right Traefik labels will automatically:
- Get a subdomain route (e.g., `myapp.yourdomain.com`)
- Get a free SSL certificate via Let's Encrypt
- Be reachable from the internet — zero manual config

---

## Prerequisites

Before running the setup script, you need:

1. **A domain name** — any domain you control (e.g., `goliath.yourdomain.com`)
2. **Wildcard DNS** — an A record for `*.yourdomain.com` pointing to the server IP (`178.156.152.148`)
3. **SSH access** — root or sudo access to the Goliath server
4. **Ports 80 and 443** available (not used by another service)

### DNS Setup

Go to your domain registrar's DNS settings and add:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `*.yourdomain.com` | `178.156.152.148` | 300 |

Or if using a subdomain for Goliath apps:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | `*.apps.yourdomain.com` | `178.156.152.148` | 300 |

**Verify DNS is working** (after a few minutes):
```bash
dig hello.yourdomain.com +short
# Should return: 178.156.152.148
```

---

## Setup Instructions

### Step 1: SSH into the server

```bash
ssh goliath@178.156.152.148
```

### Step 2: Configure environment variables

```bash
cd /opt/goliath/infrastructure
cp .env.example .env
nano .env
```

Fill in the required values:

```env
# Your domain — all apps will be at *.GOLIATH_DOMAIN
GOLIATH_DOMAIN=apps.yourdomain.com

# Email for Let's Encrypt SSL certificate notifications
ACME_EMAIL=your@email.com

# Optional: Traefik dashboard auth
# Generate with: htpasswd -nb admin yourpassword
# Double the dollar signs: $ → $$
TRAEFIK_DASHBOARD_AUTH=admin:$$apr1$$...
```

### Step 3: Run the setup script

```bash
sudo bash setup-phase1.sh
```

The script will:
1. Install Docker Engine (if not already installed)
2. Verify Docker Compose is available
3. Create the `goliath-apps` Docker network
4. Check port 80 and 443 availability
5. Start the Traefik reverse proxy container
6. Deploy the Hello World test app
7. Verify DNS resolution
8. Run an end-to-end HTTPS test
9. Write a status file at `infrastructure/phase1-status.json`

### Step 4: Verify

```bash
# Check containers are running
docker ps

# You should see:
# goliath-traefik       — Traefik reverse proxy
# goliath-hello-world   — Test app

# Test the hello world app
curl -I https://hello.yourdomain.com
# Should return: HTTP/2 200 with valid SSL
```

Open in your browser: `https://hello.yourdomain.com`

You should see the "Pipeline Active" page confirming everything works.

---

## What's Automated vs Manual

| Task | Who Does It |
|------|-------------|
| Installing Docker Engine | **Automated** (setup script) |
| Installing Docker Compose | **Automated** (setup script) |
| Creating Docker network | **Automated** (setup script) |
| Starting Traefik container | **Automated** (setup script) |
| Deploying test app | **Automated** (setup script) |
| Firewall rules (if UFW active) | **Automated** (setup script) |
| **DNS wildcard record** | **Manual** — you must add this at your registrar |
| **Choosing a domain** | **Manual** — pick the domain for app deployments |
| **Filling in .env** | **Manual** — configure domain + email |
| **Running the script via SSH** | **Manual** — SSH in and run it |

---

## File Layout After Setup

```
/opt/goliath/infrastructure/
├── docker-compose.traefik.yml      # Traefik reverse proxy config
├── docker-compose.hello-world.yml  # Hello World test app
├── hello-world/
│   └── index.html                  # Test app HTML page
├── .env                            # Your domain + email config (gitignored)
├── .env.example                    # Template for .env
├── setup-phase1.sh                 # The setup script
└── phase1-status.json              # Auto-generated status (post-setup)
```

---

## Troubleshooting

### Traefik won't start
```bash
docker logs goliath-traefik
```
Common causes:
- Port 80 or 443 already in use → stop the conflicting service
- .env not configured → check GOLIATH_DOMAIN and ACME_EMAIL

### SSL cert not provisioning
- DNS not pointing to server → verify with `dig hello.yourdomain.com +short`
- Port 80 blocked by firewall → Let's Encrypt needs HTTP challenge on port 80
- Wait 1-2 minutes → cert provisioning is not instant

### Hello World app not reachable
```bash
# Check if container is running
docker ps | grep hello

# Check Traefik logs
docker logs goliath-traefik | tail -20

# Test locally (bypassing DNS)
curl -H "Host: hello.yourdomain.com" http://localhost
```

### Port conflicts with nginx
If nginx is currently using port 80:
```bash
sudo systemctl stop nginx
sudo systemctl disable nginx  # Prevent restart on boot
```
Then re-run the setup script. Traefik replaces nginx for app routing.

---

## Cleanup

To remove everything Phase 1 installed:

```bash
cd /opt/goliath/infrastructure

# Stop and remove containers
docker compose -f docker-compose.hello-world.yml down
docker compose -f docker-compose.traefik.yml down

# Remove the Docker network
docker network rm goliath-apps

# Remove Docker volumes (SSL certs will be lost)
docker volume rm goliath-traefik-letsencrypt goliath-traefik-logs

# Optionally uninstall Docker
# sudo apt-get remove -y docker-ce docker-ce-cli containerd.io
```

---

## What Comes Next

### Phase 2 — Preview + Ship Pipeline
- Preview panel in the Goliath GUI (iframe + debug console)
- `PREVIEW_READY` signal block from DevOps agent
- "Ship It" button → builds Docker image → deploys with Traefik labels
- Auto-subdomain assignment: `{app-name}.yourdomain.com`

### Phase 3 — Domain + Polish
- Custom domain input in GUI
- App management dashboard (start/stop/restart/logs)
- Rollback support
- Resource limits per container

---

*This document is part of the App Builder Platform. See the full PRD at /opt/goliath/docs/prd-app-builder-platform.md.*
