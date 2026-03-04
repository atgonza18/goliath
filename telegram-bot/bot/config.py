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

# Agent runner backend: "sdk" uses Claude Agent SDK (multi-step, full tools)
#                       "cli" uses claude --print (single-shot, legacy)
AGENT_RUNNER_BACKEND = os.getenv("AGENT_RUNNER_BACKEND", "sdk")

# ---------------------------------------------------------------------------
# Agent model configuration (no cost guardrails — running on Max plan)
# ---------------------------------------------------------------------------
# Default model for most subagent calls.  Used when an agent definition
# doesn't specify its own model override.
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")

# Heavy model for agents that need deep reasoning (constraint analysis,
# construction sequencing, CPM scheduling, cost analysis, transcript parsing).
AGENT_MODEL_HEAVY = os.getenv("AGENT_MODEL_HEAVY", "claude-opus-4-6")

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
RECALL_API_BASE_URL = os.getenv("RECALL_API_BASE_URL", "https://us-west-2.recall.ai")
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
# Proactive Follow-Up Engine config (replaces old Escalation Engine)
# ---------------------------------------------------------------------------
# SQLite DB for tracking follow-up state (separate from memory.db)
PROACTIVE_FOLLOWUP_DB_PATH = DATA_DIR / "proactive_followup.db"

# Cooldown between follow-up tiers (days). After sending a Tier N follow-up,
# wait this many days before advancing to Tier N+1.
PROACTIVE_FOLLOWUP_COOLDOWN_DAYS = int(os.getenv("PROACTIVE_FOLLOWUP_COOLDOWN_DAYS", "5"))

# Maximum follow-up tier (1=Helpful, 2=Firmer with alternatives, 3=Loop in leadership)
PROACTIVE_FOLLOWUP_MAX_TIER = 3

# Daily report generation time (CT timezone, 24-hour format)
# Generates ONE consolidated PDF report per day instead of individual Telegram messages
PROACTIVE_FOLLOWUP_REPORT_TIME = (7, 0)   # 7:00 AM CT — after morning report

# Legacy escalation config — kept for backward compatibility during migration
# These are now aliases for the new config names
ESCALATION_DB_PATH = PROACTIVE_FOLLOWUP_DB_PATH
ESCALATION_SCAN_TIMES = [(7, 0)]  # Single daily report time
ESCALATION_COOLDOWN_DAYS = PROACTIVE_FOLLOWUP_COOLDOWN_DAYS
ESCALATION_MAX_LEVEL = PROACTIVE_FOLLOWUP_MAX_TIER
ESCALATION_MEDIUM_HORIZON_DAYS = int(os.getenv("ESCALATION_MEDIUM_HORIZON_DAYS", "7"))

# ---------------------------------------------------------------------------
# Email Reply Log config (reply-awareness for follow-up generators)
# ---------------------------------------------------------------------------
# JSON file that logs every detected email reply matched to a constraint.
# The proactive follow-up engine and morning report check this log so they
# don't generate blind follow-up drafts for constraints that already received
# a reply. Entries older than REPLY_LOG_RETENTION_HOURS are auto-pruned.
REPLY_LOG_PATH = DATA_DIR / "email_reply_log.json"
REPLY_LOG_RETENTION_HOURS = int(os.getenv("REPLY_LOG_RETENTION_HOURS", "72"))
REPLY_LOG_AWARENESS_HOURS = int(os.getenv("REPLY_LOG_AWARENESS_HOURS", "48"))

# ---------------------------------------------------------------------------
# Follow-Up Queue config
# ---------------------------------------------------------------------------
# SQLite DB for follow-up queue state (commitments from meetings, separate from memory.db)
FOLLOWUP_DB_PATH = DATA_DIR / "followup.db"

# Follow-up scan times (CT timezone, 24-hour format) — for commitment follow-ups
FOLLOWUP_SCAN_TIMES = [
    (10, 0),   # 10:00 AM CT
    (16, 0),   # 4:00 PM CT
]

# Constraints approaching need-by date within this many hours trigger a reminder
FOLLOWUP_HORIZON_HOURS = int(os.getenv("FOLLOWUP_HORIZON_HOURS", "48"))

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

# ---------------------------------------------------------------------------
# Hauger DSC Summary config (special handling for Josh Hauger's weekly emails)
# ---------------------------------------------------------------------------
# Josh Hauger sends weekly DSC summary emails that contain BOTH constraint
# status updates AND production data. These should NOT create new constraints
# in ConstraintsPro (that would be circular — the data comes FROM ConstraintsPro).
# Instead: constraint content -> append as notes on existing constraints;
#          production content -> store as intel in MemoryStore + project files.
#
# Sender patterns (case-insensitive partial match on email address).
HAUGER_SUMMARY_SENDERS = ['hauger', 'hogger']

# Subject pattern that identifies Hauger's DSC summary emails specifically.
# These have a distinctive "DSC - " prefix with production & constraints data.
HAUGER_SUMMARY_SUBJECT_PREFIX = 'dsc - '

# ---------------------------------------------------------------------------
# Agent Runner Retry / Resilience config
# ---------------------------------------------------------------------------
# Max retry attempts per agent call (includes the initial attempt).
# e.g., 3 means: 1 initial + 2 retries.
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "3"))

# Base delay in seconds for exponential backoff between retries.
RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "2.0"))

# Maximum delay cap in seconds (backoff won't exceed this).
RETRY_MAX_DELAY = float(os.getenv("RETRY_MAX_DELAY", "30.0"))

# Whether to add random jitter to backoff delays (recommended: True).
RETRY_JITTER = os.getenv("RETRY_JITTER", "true").lower() in ("true", "1", "yes")

# Circuit breaker: after this many consecutive failures for the same agent,
# stop calling it for the cooldown period.
RETRY_CIRCUIT_BREAKER_THRESHOLD = int(os.getenv("RETRY_CIRCUIT_BREAKER_THRESHOLD", "3"))

# Circuit breaker cooldown in seconds (default: 5 minutes).
RETRY_CIRCUIT_BREAKER_COOLDOWN = float(os.getenv("RETRY_CIRCUIT_BREAKER_COOLDOWN", "300.0"))

# ---------------------------------------------------------------------------
# Experience Replay config (self-evolving lessons from past interactions)
# ---------------------------------------------------------------------------
# Master switch — set to False to disable lesson extraction entirely.
EXPERIENCE_REPLAY_ENABLED = os.getenv("EXPERIENCE_REPLAY_ENABLED", "true").lower() in ("true", "1", "yes")

# Minimum number of low-scoring reflections with the same pattern before
# a lesson is generated. Lower = more sensitive, higher = fewer false positives.
EXPERIENCE_REPLAY_MIN_REFLECTIONS = int(os.getenv("EXPERIENCE_REPLAY_MIN_REFLECTIONS", "2"))

# Maximum lessons stored in the lessons_learned table. Oldest low-confidence
# lessons are pruned when this cap is exceeded.
EXPERIENCE_REPLAY_MAX_LESSONS = int(os.getenv("EXPERIENCE_REPLAY_MAX_LESSONS", "50"))

# ---------------------------------------------------------------------------
# Prompt Self-Review config (V4 — scheduled heuristic audit of agent prompts)
# ---------------------------------------------------------------------------
# Master switch — set to False to disable prompt self-review entirely.
PROMPT_REVIEW_ENABLED = os.getenv("PROMPT_REVIEW_ENABLED", "true").lower() in ("true", "1", "yes")

# Prompts longer than this (in characters) trigger a "warning" finding.
PROMPT_REVIEW_MAX_LENGTH_WARNING = int(os.getenv("PROMPT_REVIEW_MAX_LENGTH_WARNING", "5000"))

# Agent definition files not modified in this many days trigger a staleness "info".
PROMPT_REVIEW_STALENESS_DAYS = int(os.getenv("PROMPT_REVIEW_STALENESS_DAYS", "30"))


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
