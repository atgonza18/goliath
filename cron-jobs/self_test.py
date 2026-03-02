#!/usr/bin/env python3
"""
GOLIATH Self-Test — Post-deploy and on-demand system verification.

Runs a suite of quick tests to verify all subsystems are functional:
  - Bot webhook server responds
  - Web API health endpoint responds
  - Claude CLI is accessible
  - All SQLite databases open without errors
  - All 12 project directories exist
  - Email poller credentials are set
  - Cron reports directory exists and is writable

Output: Pass/fail for each test, summary line. Sends results to Telegram.

Usage:
    # Standalone:
    python self_test.py

    # Importable (returns structured results):
    from self_test import run_self_test
    results = run_self_test()
    print(results["summary"])

    # Quick summary for startup (returns HTML string):
    from self_test import run_self_test_summary
    html = run_self_test_summary()
"""

import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

CT = ZoneInfo("America/Chicago")

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

# Paths
DATA_DIR = REPO_ROOT / "telegram-bot" / "data"
PROJECTS_DIR = REPO_ROOT / "projects"
CRON_REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"

# Expected project directories
PROJECTS = [
    "union-ridge", "duff", "salt-branch", "blackford",
    "delta-bobcat", "tehuacana", "three-rivers", "scioto-ridge",
    "mayes", "graceland", "pecan-prairie", "duffy-bess",
]

# Databases to verify
DATABASES = {
    "memory.db": DATA_DIR / "memory.db",
    "followup.db": DATA_DIR / "followup.db",
    "proactive_followup.db": DATA_DIR / "proactive_followup.db",
}

MAX_MSG_LEN = 4000


# ======================================================================
# Individual tests
# ======================================================================

def test_webhook_server() -> tuple[bool, str]:
    """Test if the bot's webhook server responds on port 8000."""
    try:
        import requests
        resp = requests.get("http://localhost:8000/webhook/health", timeout=5)
        if resp.status_code == 200:
            return True, "Webhook server responding on :8000"
        return False, f"Webhook returned status {resp.status_code}"
    except Exception as e:
        return False, f"Webhook server not reachable: {type(e).__name__}"


def test_web_api() -> tuple[bool, str]:
    """Test if the web platform API responds."""
    try:
        import requests
        # Try port 80 first, then 3001
        for port in [80, 3001]:
            try:
                resp = requests.get(f"http://localhost:{port}/api/health", timeout=5)
                if resp.status_code == 200:
                    return True, f"Web API responding on :{port}"
            except Exception:
                continue
        return False, "Web API not responding on port 80 or 3001"
    except Exception as e:
        return False, f"Cannot test web API: {type(e).__name__}"


def test_claude_cli() -> tuple[bool, str]:
    """Test if the Claude CLI is accessible."""
    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_AGENT_SDK_VERSION", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=15,
            env=env,
        )
        if result.returncode == 0:
            version = result.stdout.strip()[:80]
            return True, f"Claude CLI: {version}"
        return False, f"Claude CLI returned exit code {result.returncode}"
    except FileNotFoundError:
        return False, "Claude CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "Claude CLI timed out"
    except Exception as e:
        return False, f"Claude CLI error: {type(e).__name__}: {e}"


def test_databases() -> tuple[bool, str]:
    """Test that all SQLite databases can be opened and queried."""
    results = []
    all_ok = True

    for db_name, db_path in DATABASES.items():
        if not db_path.exists():
            results.append(f"{db_name}: MISSING")
            all_ok = False
            continue

        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            # Quick query to verify the database is functional
            cursor = conn.execute("SELECT count(*) FROM sqlite_master")
            table_count = cursor.fetchone()[0]
            size_mb = db_path.stat().st_size / (1024 * 1024)
            conn.close()
            results.append(f"{db_name}: OK ({table_count} tables, {size_mb:.1f}MB)")
        except sqlite3.Error as e:
            results.append(f"{db_name}: ERROR ({e})")
            all_ok = False
        except Exception as e:
            results.append(f"{db_name}: ERROR ({type(e).__name__})")
            all_ok = False

    detail = "; ".join(results)
    return all_ok, detail


def test_project_directories() -> tuple[bool, str]:
    """Test that all 12 project directories exist."""
    missing = []
    for project in PROJECTS:
        project_dir = PROJECTS_DIR / project
        if not project_dir.exists():
            missing.append(project)

    if not missing:
        return True, f"All {len(PROJECTS)} project directories exist"
    return False, f"Missing {len(missing)} project dirs: {', '.join(missing)}"


def test_email_credentials() -> tuple[bool, str]:
    """Test that email poller credentials are configured in the environment."""
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "") or os.environ.get("GOLIATH_GMAIL", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "") or os.environ.get("GOLIATH_GMAIL_APP_PASSWORD", "")

    if gmail_addr and gmail_pass:
        # Mask the address for display
        masked = gmail_addr[:3] + "***@" + gmail_addr.split("@")[-1] if "@" in gmail_addr else "***"
        return True, f"Email credentials set ({masked})"

    missing = []
    if not gmail_addr:
        missing.append("GMAIL_ADDRESS")
    if not gmail_pass:
        missing.append("GMAIL_APP_PASSWORD")
    return False, f"Missing email credentials: {', '.join(missing)}"


def test_cron_reports_dir() -> tuple[bool, str]:
    """Test that the cron reports directory exists and is writable."""
    if not CRON_REPORTS_DIR.exists():
        try:
            CRON_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            return True, "Reports directory created"
        except Exception as e:
            return False, f"Cannot create reports directory: {e}"

    # Check if writable
    test_file = CRON_REPORTS_DIR / ".write_test"
    try:
        test_file.write_text("test")
        test_file.unlink()
        # Count existing reports
        reports = list(CRON_REPORTS_DIR.glob("*"))
        report_count = len([f for f in reports if f.is_file() and f.name != ".write_test"])
        return True, f"Reports directory writable ({report_count} report files)"
    except Exception as e:
        return False, f"Reports directory not writable: {e}"


def test_env_file() -> tuple[bool, str]:
    """Test that the .env file exists and has critical variables."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return False, ".env file not found"

    # Check critical vars
    critical_vars = ["TELEGRAM_BOT_TOKEN", "REPORT_CHAT_ID"]
    missing = [v for v in critical_vars if not os.environ.get(v)]

    if missing:
        return False, f".env exists but missing: {', '.join(missing)}"
    return True, ".env loaded with critical variables"


# ======================================================================
# Test runner
# ======================================================================

def run_self_test() -> dict:
    """Run all self-tests and return structured results.

    Returns:
        Dict with keys: timestamp, tests (list of dicts), passed, failed, summary.
    """
    now = datetime.now(CT)

    # Define all tests in order
    test_suite = [
        ("Webhook Server", test_webhook_server),
        ("Web API", test_web_api),
        ("Claude CLI", test_claude_cli),
        ("Databases", test_databases),
        ("Project Directories", test_project_directories),
        ("Email Credentials", test_email_credentials),
        ("Cron Reports Directory", test_cron_reports_dir),
        ("Environment File", test_env_file),
    ]

    tests = []
    passed = 0
    failed = 0

    for name, test_func in test_suite:
        try:
            ok, detail = test_func()
        except Exception as e:
            ok = False
            detail = f"Test crashed: {type(e).__name__}: {e}"

        tests.append({
            "name": name,
            "passed": ok,
            "detail": detail,
        })

        if ok:
            passed += 1
        else:
            failed += 1

    total = passed + failed
    summary = f"{passed}/{total} tests passed"
    if failed > 0:
        summary += f" ({failed} failed)"

    return {
        "timestamp": now.isoformat(),
        "tests": tests,
        "passed": passed,
        "failed": failed,
        "total": total,
        "summary": summary,
        "all_passed": failed == 0,
    }


def run_self_test_summary() -> str:
    """Run self-tests and return a compact HTML summary for Telegram.

    This is designed to be appended to the bot startup message.
    """
    result = run_self_test()

    lines = []
    lines.append(f"<b>Self-Test: {result['summary']}</b>")

    for test in result["tests"]:
        icon = "✅" if test["passed"] else "❌"
        # Keep detail short for the startup message
        detail = test["detail"]
        if len(detail) > 80:
            detail = detail[:77] + "..."
        lines.append(f"  {icon} {test['name']}: {_html_escape(detail)}")

    return "\n".join(lines)


# ======================================================================
# Telegram sending
# ======================================================================

def _html_escape(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_telegram_message(result: dict) -> str:
    """Format self-test results as a clean HTML Telegram message."""
    lines = []

    all_passed = result["all_passed"]
    icon = "✅" if all_passed else "⚠️"

    lines.append(f"<b>{icon} GOLIATH Self-Test Results</b>")
    lines.append(f"<i>{datetime.now(CT).strftime('%Y-%m-%d %I:%M %p CT')}</i>")
    lines.append("")

    for test in result["tests"]:
        test_icon = "✅" if test["passed"] else "❌"
        lines.append(f"{test_icon} <b>{test['name']}</b>")
        lines.append(f"    {_html_escape(test['detail'])}")

    lines.append("")
    lines.append(f"<b>Summary: {result['summary']}</b>")

    if not all_passed:
        lines.append("")
        lines.append("<i>Some tests failed. Check the details above and resolve issues.</i>")

    return "\n".join(lines)


def chunk_message(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Split a message into Telegram-safe chunks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_idx = text.rfind("\n", 0, max_len)
        if split_idx == -1 or split_idx < max_len // 2:
            split_idx = max_len
        chunks.append(text[:split_idx])
        text = text[split_idx:].lstrip("\n")

    return chunks


def send_telegram_message(chat_id: str, text: str) -> bool:
    """Send a message via the Telegram Bot API using HTML parse mode."""
    import requests

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = chunk_message(text)
    success = True

    for i, chunk in enumerate(chunks):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                payload.pop("parse_mode")
                resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                print(f"Failed to send chunk {i+1}/{len(chunks)}: {resp.status_code} {resp.text}")
                success = False
            else:
                print(f"Sent chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        except Exception as e:
            print(f"Failed to send chunk {i+1}/{len(chunks)}: {e}")
            success = False

    return success


# ======================================================================
# Main (standalone execution)
# ======================================================================

def main():
    """Run self-tests and report results to stdout and Telegram."""
    print(f"[{datetime.now(CT).isoformat()}] Running GOLIATH self-test...")
    print()

    result = run_self_test()

    # Print results to stdout
    for test in result["tests"]:
        status = "PASS" if test["passed"] else "FAIL"
        print(f"  [{status}] {test['name']}: {test['detail']}")

    print()
    print(f"Summary: {result['summary']}")

    # Send to Telegram
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\nTelegram credentials not configured, skipping notification.")
        return 0 if result["all_passed"] else 1

    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()
    msg = format_telegram_message(result)
    success = send_telegram_message(chat_id, msg)

    if success:
        print("\nSelf-test results sent to Telegram.")
    else:
        print("\nFailed to send self-test results to Telegram.")

    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
