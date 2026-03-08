# PRD: App Builder Platform

**Author:** Nimrod (via DevOps Agent)
**Created:** 2026-03-07
**Last Updated:** 2026-03-07
**Status:** Unbuilt — Planning Complete
**Priority:** High
**Revision:** v1.1 — Added backend selector (Convex/Postgres), debug console, CLI build mode

---

## 1. Vision / Overview

A personal **Heroku-style platform** built directly into Goliath. The user describes an app in plain language, the DevOps agent builds it, the user previews it live inside the Goliath GUI, and then clicks **"Ship It"** to deploy it as an isolated Docker container with its own database and URL.

Each shipped app gets:
- Its own **Docker container** (fully isolated runtime)
- Its own **Postgres database** (per-app schema or dedicated container)
- An **auto-assigned subdomain** like `appname.yourdomain.com`
- Optional **custom domain** with auto-provisioned SSL via Let's Encrypt

This turns Goliath from a portfolio management tool into a **personal deployment platform** — self-hosted, zero vendor lock-in, fully integrated into the existing build workflow.

**Reference comparisons:** Railway, Render, Fly.io — but self-hosted and wired into Goliath's conversational DevOps loop.

---

## 2. User Flow (Plain English)

### Step 1: Build
> User describes the app they want → DevOps agent builds it → live preview appears in a GUI iframe panel.

The user talks to Goliath like normal: *"Build me a landing page for my solar consulting business with a contact form and email integration."* DevOps generates the code, spins up a local preview, and the GUI shows it in real time.

### Step 2: Ship
> User clicks **"Ship It"** → app is packaged into a Docker container with an isolated Postgres DB → auto-assigned a URL like `appname.yourdomain.com`.

One click. No config files. No terminal. The app goes from preview to live on the internet with its own database, isolated from every other app and from Goliath itself.

### Step 3: Domain (Optional)
> User enters a custom domain in a single input field → SSL cert is auto-generated → DNS instructions provided.

One field. One click. Traefik handles the cert. User just needs to point their DNS. Done.

---

## 3. Intent Selector (Critical UX)

**This is the single most important UX decision in the entire feature.**

Before DevOps starts any build, the GUI presents two explicit buttons:

| Button | Label | Meaning |
|--------|-------|---------|
| 🔧 | **Goliath Feature** | Patch the existing Goliath GUI/codebase |
| 🚀 | **New App** | Spin up a separate Docker container |

### Why This Matters
Without this selector, there is dangerous ambiguity. If the user says *"build me a dashboard"*, does that mean:
- Add a dashboard page to Goliath's GUI? (🔧 Goliath Feature)
- Build a standalone dashboard app and deploy it? (🚀 New App)

Getting this wrong wastes an entire build session. The intent selector **eliminates the ambiguity upfront**.

### Behavior
- **🔧 Goliath Feature** → DevOps operates in **patch mode**: edits existing files, modifies the running codebase, may trigger `RESTART_REQUIRED`.
- **🚀 New App** → DevOps operates in **container mode**: generates a new project directory, Dockerfile, docker-compose.yml, and deploys to an isolated container. Never touches Goliath's own code.

The selector appears as a persistent UI element whenever the user initiates a build-type request. It is **not optional** — one must be selected before DevOps begins work.

---

## 3b. Backend Selector (New Apps Only)

After the user selects **🚀 New App**, the GUI presents a **Backend Selector** — a second required choice before the build begins:

| Option | Label | What It Means |
|--------|-------|---------------|
| 🐘 | **Postgres** | Sidecar Postgres container in docker-compose. Fully isolated, on-server. Standard SQL. |
| ⚡ | **Convex (Cloud)** | App connects to convex.dev cloud service. DevOps generates schema + server functions. Requires Convex API key. |
| 🏠 | **Convex (Self-Hosted)** | Open-source Convex backend running in a Docker container alongside the app. Fully on-server, no external dependency. More complex. |

### Postgres Mode
- A `postgres` service is added to the app's `docker-compose.yml` as a sidecar container
- DevOps auto-generates the DB schema and migration files
- Connection string injected automatically as `DATABASE_URL` env var
- Database is destroyed with the app container (or optionally persisted via named volume)
- **Best for:** traditional apps, SQL-native developers, anything that doesn't need real-time sync

### Convex Cloud Mode
- App includes the Convex client SDK (`convex` npm package)
- DevOps generates `convex/schema.ts` and server function files
- User provides a Convex API key (stored in per-app `.env`, never in source)
- No local DB container — all data lives in Convex.dev's cloud
- **Best for:** real-time apps, fast prototyping, apps that need live subscriptions and sync

### Convex Self-Hosted Mode
- Deploys the open-source Convex backend as an additional Docker container
- All data stays on-server — zero external dependency
- More resource-intensive and requires more setup
- **Best for:** apps where data sovereignty matters and real-time sync is needed

### Backend Selector Behavior
- The 🔧 **Goliath Feature** path skips this selector entirely — it uses the existing Goliath DB/stack
- Backend choice is locked in at build time; changing it after deploy requires a redeploy
- DevOps is aware of which backend mode was selected and generates appropriate code accordingly

---

## 4. Tech Stack Decisions

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Isolation** | Docker | Industry standard. Each app is a container with its own filesystem, network, and process space. |
| **Reverse Proxy / SSL** | Traefik v3 | Auto-discovers Docker containers, auto-provisions SSL, zero manual config per app. See §5. |
| **Database** | Postgres (per-app) | Per-app isolated schema or separate Postgres container. Apps never share data. |
| **Preview** | iframe in GUI | Collapsible panel in the Goliath web interface. Shows live build output during development. |
| **Container Lifecycle** | Docker Compose | One `docker-compose.yml` per app. Managed via Docker CLI from DevOps agent. |
| **DNS / Subdomains** | Wildcard DNS + Traefik | `*.yourdomain.com` points to server. Traefik routes based on container labels. |
| **SSL** | Let's Encrypt (via Traefik) | Automatic certificate provisioning and renewal. Zero manual intervention. |

### Preview Panel Details
- Embedded iframe in the Goliath GUI
- Toggle/collapsible — doesn't take over the whole screen
- Shows the app as it's being built (hot reload where possible)
- Triggered by a `PREVIEW_READY` signal block from DevOps (analogous to `FILE_CREATED`)

### Debug Console (Required)
The preview panel includes a **real-time debug console** panel docked below the iframe — think browser DevTools but for your Docker container.

| Feature | Detail |
|---------|--------|
| **Log streaming** | WebSocket connection to container stdout/stderr |
| **Color coding** | 🔴 errors / 🟡 warnings / ⚪ info |
| **Clear button** | Wipe log history |
| **Available in** | Both preview mode AND post-ship (view live app logs) |
| **Collapsible** | Doesn't obstruct the preview by default |

This is non-negotiable for a real dev tool. Without a debug console, you're flying blind when something breaks.

### Signal Block: PREVIEW_READY
```
```PREVIEW_READY
url: http://localhost:3001
title: Solar Landing Page
```
```
When the orchestrator sees this block, the GUI opens the preview panel with the iframe pointed at the given URL.

---

## 5. Why Traefik over nginx

nginx is the current reverse proxy on the server and works fine for static routing. But for **dynamic container-based deployments**, Traefik is purpose-built:

| Capability | nginx | Traefik |
|------------|-------|---------|
| Auto-discover new Docker containers | ❌ Manual config edit per app | ✅ Watches Docker socket automatically |
| Auto-provision SSL certs | ❌ Requires certbot + cron + reload | ✅ Built-in Let's Encrypt ACME |
| Route by container label | ❌ Not supported | ✅ Native — just add labels to docker-compose |
| Zero-downtime config changes | ❌ Requires `nginx -s reload` | ✅ Real-time, no reload needed |
| Dashboard / monitoring | ❌ Manual setup | ✅ Built-in web dashboard |

### How It Works
1. A new app container starts with Docker labels like:
   ```yaml
   labels:
     - "traefik.http.routers.myapp.rule=Host(`myapp.yourdomain.com`)"
     - "traefik.http.routers.myapp.tls.certresolver=letsencrypt"
   ```
2. Traefik sees the container appear, reads the labels, creates the route, provisions the SSL cert.
3. The app is live at `https://myapp.yourdomain.com` within seconds.
4. No config files edited. No services reloaded. No manual steps.

**Traefik was designed for exactly this pattern.** nginx was not.

### Coexistence with Existing nginx
During transition, nginx continues to handle Goliath's own traffic (port 8000). Traefik handles all container-deployed apps on ports 80/443. Eventually, Traefik can replace nginx entirely if desired.

---

## 5b. CLI Build Mode — How Apps Get Built

The user never writes code. The **Goliath DevOps agent (CLI)** writes everything.

### The Full Conversation Flow

```
User: "Build me a SaaS invoicing tool with a dashboard, PDF export, and client management"
       ↓
Nimrod: routes to DevOps with intent = New App, backend = Postgres
       ↓
DevOps: generates the complete artifact set:
   ├── src/             (React + Next.js frontend)
   ├── api/             (Express or FastAPI backend)
   ├── convex/ OR db/   (Convex schema OR Postgres migrations)
   ├── Dockerfile
   ├── docker-compose.yml  (app + db + Traefik labels)
   └── .env             (auto-generated secrets, DB URL, API keys)
       ↓
DevOps emits: PREVIEW_READY block
       ↓
GUI: opens preview panel + debug console
       ↓
User: reviews, tests, hits "Ship It"
       ↓
System: docker compose up --build → Traefik picks it up → live at subdomain
```

### What DevOps Generates Per App (Full Artifact Set)

| Artifact | Description |
|----------|-------------|
| Frontend | React or Next.js (based on what the app needs) |
| Backend | Express/FastAPI API layer OR Convex server functions |
| Database | Postgres migrations (SQL) OR Convex schema (`schema.ts`) |
| `Dockerfile` | Multi-stage build for production efficiency |
| `docker-compose.yml` | All services wired together + Traefik routing labels |
| `.env` | Per-app secrets, DB URL, API keys (never committed to git) |
| `README.md` | What the app does, how to run it locally, env vars needed |

### No Manual Coding Required
The user's only job: describe what they want in plain language. DevOps figures out the tech stack, generates everything, and tells the GUI when it's ready to preview. If something is broken, the debug console shows why and the user can say "fix it" — DevOps patches and rebuilds.

---

## 6. 3-Phase Build Plan

### Phase 1 — Foundation (1–2 DevOps Sessions)

**Goal:** Docker + Traefik installed and working end-to-end with one test app.

| Task | Details |
|------|---------|
| Install Docker Engine | `apt install docker.io` or official Docker CE repo |
| Install Docker Compose v2 | Plugin for `docker compose` CLI |
| Install Traefik | Run as a Docker container itself, watching the Docker socket |
| Configure Traefik | HTTPS entrypoint, Let's Encrypt resolver, Docker provider |
| Set up base networking | Docker network `goliath-apps` for inter-container communication |
| Wildcard DNS | Point `*.yourdomain.com` to server IP |
| Test with dummy app | Deploy a simple "Hello World" container, verify it's reachable at `test.yourdomain.com` with valid SSL |

**Exit criteria:** A dummy container is live at a subdomain with auto-SSL. No manual config was needed beyond labels.

---

### Phase 2 — Preview + Ship Pipeline (2–3 Sessions)

**Goal:** Full build → preview → ship workflow working from the GUI.

| Task | Details |
|------|---------|
| Preview panel in GUI | iframe + toggle button, collapsible side/bottom panel |
| `PREVIEW_READY` signal block | DevOps emits this; orchestrator/GUI opens the preview panel |
| DevOps learns containerization | Agent generates `Dockerfile` + `docker-compose.yml` for each app |
| App project structure | Standard template: `/opt/goliath/apps/{app-name}/` with code, Dockerfile, compose file |
| "Ship It" button in GUI | Calls `/api/ship` endpoint |
| `/api/ship` endpoint | In `web_api.py`: builds image, starts container with Traefik labels, returns URL |
| Auto-subdomain assignment | `{app-name}.yourdomain.com` derived from app name, sanitized |
| Intent selector UI | 🔧 / 🚀 buttons presented before any build (see §3) |

**Exit criteria:** User can describe an app, see it in the preview panel, click "Ship It", and visit it at a live subdomain URL.

---

### Phase 3 — Domain + Polish (1–2 Sessions)

**Goal:** Custom domains, management dashboard, and operational polish.

| Task | Details |
|------|---------|
| Custom domain input | Single text field in GUI; user enters `myapp.com` |
| Traefik label update | DevOps updates container labels to add the custom domain route |
| DNS instructions | GUI shows the user exactly what DNS record to create (A record → server IP) |
| Auto-SSL for custom domains | Traefik provisions cert for the custom domain via HTTP-01 challenge |
| App management dashboard | List all running apps: name, URL, status, uptime, resource usage |
| App controls | Stop / restart / kill / view logs per app |
| Rollback | Git-tag each deploy; rollback = redeploy previous tagged version |
| Resource limits | Per-container CPU/memory limits to prevent one app from starving others |

**Exit criteria:** User can assign a custom domain, manage running apps from a dashboard, and roll back a bad deploy.

---

## 7. What's NOT Included (Out of Scope)

These are explicitly **out of scope** for the initial build. They may be revisited later.

| Item | Reason |
|------|--------|
| **Multi-server scaling** | Single Hetzner VPS is sufficient until traffic demands otherwise. Premature optimization. |
| **CI/CD from external repos** | Apps are built conversationally through Goliath, not pushed from GitHub. |
| **Per-app user authentication** | Future feature. For now, apps are public or the user handles auth in their app code. |
| **Billing / metering** | This is a personal platform, not a SaaS. No billing needed. |
| **Multi-language support** | DevOps will default to Python/Node.js. Other runtimes can be added later via Dockerfile flexibility. |
| **Persistent storage volumes** | Database covers persistence. File uploads / blob storage is a future concern. |
| **App marketplace / templates** | Build from conversation, not from a template gallery. Templates may come later. |

---

## 8. Current Status

| Item | Status |
|------|--------|
| **PRD** | ✅ Complete (this document, v1.1) |
| **Docker on server** | ❌ Not installed |
| **Traefik on server** | ❌ Not installed |
| **Preview panel in GUI** | ❌ Not built |
| **Debug console** | ❌ Not built |
| **Ship pipeline** | ❌ Not built |
| **Intent selector (🔧 / 🚀)** | ❌ Not built |
| **Backend selector (Postgres/Convex)** | ❌ Not built |
| **Convex cloud integration** | ❌ Not built |
| **Convex self-hosted option** | ❌ Not built |
| **App management dashboard** | ❌ Not built |
| **Custom domain support** | ❌ Not built |

### Current Server State (as of 2026-03-07)
- Goliath GUI runs on **port 8000**
- **nginx** handles current routing (will coexist with Traefik initially)
- No Docker installed
- No container infrastructure
- Everything in this PRD is **greenfield**

### Prerequisites Before Any Build Work
1. Docker Engine must be installed
2. Traefik must be installed and configured
3. Wildcard DNS must be set up
4. These are all **Phase 1** tasks

---

## 9. Reference Comparison

| Feature | Railway | Render | Fly.io | **Goliath App Builder** |
|---------|---------|--------|--------|------------------------|
| Deploy from conversation | ❌ | ❌ | ❌ | ✅ |
| Self-hosted | ❌ | ❌ | ❌ | ✅ |
| Auto-SSL | ✅ | ✅ | ✅ | ✅ (via Traefik) |
| Custom domains | ✅ | ✅ | ✅ | ✅ (Phase 3) |
| Postgres (per-app) | ✅ | ✅ | ✅ | ✅ |
| Convex backend option | ❌ | ❌ | ❌ | ✅ (Cloud or Self-Hosted) |
| Live preview before deploy | ❌ | ❌ | ❌ | ✅ |
| Real-time debug console | Partial | Partial | Partial | ✅ (WebSocket log stream) |
| No vendor lock-in | ❌ | ❌ | ❌ | ✅ |
| Integrated with build agent | ❌ | ❌ | ❌ | ✅ |
| Generates full artifact set | ❌ | ❌ | ❌ | ✅ (code + Docker + DB + .env) |
| Free (self-hosted) | ❌ | Partial | Partial | ✅ |

The key differentiator: **no other platform lets you describe an app in plain language, preview it live, and ship it with one click** — all from a single integrated interface.

---

## Appendix: File & Directory Conventions

```
/opt/goliath/
├── apps/                              # All deployed apps live here
│   └── {app-name}/
│       ├── src/                       # App source code (generated by DevOps)
│       ├── Dockerfile                 # Generated by DevOps
│       ├── docker-compose.yml         # Generated by DevOps, includes Traefik labels
│       └── .env                       # Per-app environment variables
├── traefik/
│   ├── traefik.yml                    # Traefik static config
│   ├── acme.json                      # Let's Encrypt cert storage (chmod 600)
│   └── docker-compose.yml             # Traefik's own compose file
└── docs/
    └── prd-app-builder-platform.md    # This document
```

---

*This PRD represents the complete vision as discussed. Phase 1 is the next actionable step.*
