import os
import re
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

# ---------------------------------------------------------------------------
# Recall.ai Meeting Bot config (automated Teams transcription)
# ---------------------------------------------------------------------------
RECALL_API_KEY = os.getenv("RECALL_API_KEY", "")
RECALL_API_BASE_URL = os.getenv("RECALL_API_BASE_URL", "https://us-east-1.recall.ai")
RECALL_BOT_NAME = os.getenv("RECALL_BOT_NAME", "Aaron Gonzalez")

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
    "transcripts",
]

# Central constraints report folder (portfolio-wide, from Joshua Hauger)
CONSTRAINTS_REPORTS_DIR = REPO_ROOT / "dsc-constraints-production-reports"

# ---------------------------------------------------------------------------
# Escalation Engine config
# ---------------------------------------------------------------------------
# SQLite DB for tracking escalation state (separate from memory.db)
ESCALATION_DB_PATH = DATA_DIR / "escalation.db"

# Cooldown between escalation levels (days). After sending a Level N email,
# wait this many days before escalating to Level N+1.
ESCALATION_COOLDOWN_DAYS = int(os.getenv("ESCALATION_COOLDOWN_DAYS", "5"))

# Maximum escalation level (1=Helpful, 2=Firm, 3=Leadership CC)
ESCALATION_MAX_LEVEL = 3

# Escalation scan times (CT timezone, 24-hour format)
ESCALATION_SCAN_TIMES = [
    (9, 0),    # 9:00 AM CT
    (13, 0),   # 1:00 PM CT
    (17, 0),   # 5:00 PM CT
]

# For MEDIUM-priority constraints: only escalate if need-by date is within
# this many days from today.
ESCALATION_MEDIUM_HORIZON_DAYS = int(os.getenv("ESCALATION_MEDIUM_HORIZON_DAYS", "7"))

# ---------------------------------------------------------------------------
# Follow-Up Queue config
# ---------------------------------------------------------------------------
# SQLite DB for follow-up queue state (separate from memory.db and escalation.db)
FOLLOWUP_DB_PATH = DATA_DIR / "followup.db"

# Follow-up scan times (CT timezone, 24-hour format) — staggered from escalation
FOLLOWUP_SCAN_TIMES = [
    (10, 0),   # 10:00 AM CT
    (16, 0),   # 4:00 PM CT
]

# Constraints approaching need-by date within this many hours trigger a reminder
FOLLOWUP_HORIZON_HOURS = int(os.getenv("FOLLOWUP_HORIZON_HOURS", "48"))

# ---------------------------------------------------------------------------
# Constraint Heartbeat config
# ---------------------------------------------------------------------------
# Snapshot storage directory
HEARTBEAT_SNAPSHOT_DIR = REPO_ROOT / "data" / "constraint_snapshots"

# Heartbeat interval in seconds (default: 3600 = 1 hour)
HEARTBEAT_INTERVAL_SECONDS = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "3600"))

# ---------------------------------------------------------------------------
# Constraint Auto-Logger config (Email -> ConstraintsPro bridge)
# ---------------------------------------------------------------------------
# Known senders of constraint-related emails (case-insensitive partial match
# on email address). Emails from these senders classified as "constraints" by
# the email poller will also trigger auto-logging to ConstraintsPro.
CONSTRAINT_EMAIL_SENDERS = ['hauger', 'hogger']

# Subject keywords that indicate constraint content — if a non-POD,
# non-schedule email has one of these in the subject AND attachments,
# it's likely a constraints email worth auto-logging.
CONSTRAINT_EMAIL_KEYWORDS = [
    'constraint', 'constraints',
    'production & constraints', 'production and constraints',
    'blocker', 'blockers',
    'open items', 'open issues',
]


def match_project_key(text: str) -> str | None:
    """Try to find a portfolio project name within a text string.

    Returns the project key (e.g., 'salt-branch') or None if no match.
    Uses word-boundary matching and checks longest names first to avoid
    false positives (e.g., 'Duff' matching inside 'Duffy BESS').
    """
    if not text:
        return None
    text_lower = text.lower()
    # Sort by name length descending so longer names match first
    sorted_projects = sorted(
        PROJECTS.items(),
        key=lambda x: len(x[1]["name"]),
        reverse=True,
    )
    for key, info in sorted_projects:
        name = info["name"].lower()
        # Use lookaround that treats underscores as non-word chars
        # \b fails with underscores because \b sees _ as a word character
        pattern = r'(?<![a-zA-Z0-9])' + re.escape(name) + r'(?![a-zA-Z0-9])'
        if re.search(pattern, text_lower):
            return key
    return None
