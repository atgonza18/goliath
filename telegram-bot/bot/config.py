import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from repo root
# Auto-detect environment: Hetzner (/opt/goliath) or Codespaces (/workspaces/goliath)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # /opt/goliath (Hetzner) or /workspaces/goliath (Codespaces)
load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your-token-here":
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. "
        "Edit .env in the repo root and paste your BotFather token."
    )

# Optional security: whitelist of allowed chat IDs
_raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
ALLOWED_CHAT_IDS: set[int] | None = (
    {int(x.strip()) for x in _raw_ids.split(",") if x.strip()}
    if _raw_ids else None
)

PROJECTS_DIR = REPO_ROOT / "projects"
CLAUDE_MD_PATH = REPO_ROOT / "Claude.md"

# Memory system
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MEMORY_DB_PATH = DATA_DIR / "memory.db"

# Project registry — single source of truth
PROJECTS: dict[str, dict] = {
    "union-ridge":   {"name": "Union Ridge",   "number": 1},
    "duff":          {"name": "Duff",           "number": 2},
    "salt-branch":   {"name": "Salt Branch",    "number": 3},
    "blackford":     {"name": "Blackford",      "number": 4},
    "delta-bobcat":  {"name": "Delta Bobcat",   "number": 5},
    "tehuacana":     {"name": "Tehuacana",      "number": 6},
    "three-rivers":  {"name": "Three Rivers",   "number": 7},
    "scioto-ridge":  {"name": "Scioto Ridge",   "number": 8},
    "mayes":         {"name": "Mayes",          "number": 9},
    "graceland":     {"name": "Graceland",      "number": 10},
    "pecan-prairie": {"name": "Pecan Prairie",  "number": 11},
    "duffy-bess":    {"name": "Duffy BESS",     "number": 12},
}

# Webhook / Power Automate integration
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))
WEBHOOK_AUTH_TOKEN = os.getenv("WEBHOOK_AUTH_TOKEN", "")
TEAMS_INCOMING_WEBHOOK_URL = os.getenv("TEAMS_INCOMING_WEBHOOK_URL", "")
REPORT_CHAT_ID = os.getenv("REPORT_CHAT_ID", "")

# Gmail SMTP/IMAP integration (for email relay via Power Automate)
# Support both GMAIL_ADDRESS and legacy GOLIATH_GMAIL env var names
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "") or os.getenv("GOLIATH_GMAIL", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "") or os.getenv("GOLIATH_GMAIL_APP_PASSWORD", "")
GMAIL_IMAP_HOST = os.getenv("GMAIL_IMAP_HOST", "imap.gmail.com")
GMAIL_SMTP_HOST = os.getenv("GMAIL_SMTP_HOST", "smtp.gmail.com")

# Outbound relay: send approved emails TO this address (user's MasTec Outlook)
# so Power Automate can pick them up and forward from the user's work email.
RELAY_TO_ADDRESS = os.getenv("RELAY_TO_ADDRESS", "")

# Subfolder names within each project
PROJECT_SUBFOLDERS = [
    "constraints",
    "schedule",
    "project-details/engineering",
    "project-details/materials",
    "project-details/location",
    "project-details/budget",
    "project-directory",
    "pod",
]
