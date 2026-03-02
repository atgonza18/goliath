#!/usr/bin/env python3
"""
GOLIATH Central Health Monitor — Comprehensive system health checking.

Checks all critical subsystems and reports status:
  - Service health (goliath-bot, goliath-web systemd units)
  - Scheduler task health (database freshness, webhook server)
  - Cron job health (expected report files)
  - Database health (size, integrity, corruption checks)
  - Disk usage
  - Network health (web API, Cloudflare tunnel, Gmail credentials)

Designed to be both importable (returns structured dict) and standalone
(sends formatted Telegram message when issues are found).

Usage:
    # Standalone — runs all checks and reports to Telegram:
    python health_monitor.py

    # Importable — returns structured health data:
    from health_monitor import run_health_check
    result = run_health_check()
    print(result["overall_status"])  # "healthy", "degraded", or "critical"
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
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
CRON_REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"
PROJECTS_DIR = REPO_ROOT / "projects"

# Database paths
DATABASES = {
    "memory.db": DATA_DIR / "memory.db",
    "followup.db": DATA_DIR / "followup.db",
    "proactive_followup.db": DATA_DIR / "proactive_followup.db",
}

# Service definitions (name -> memory limit in MB)
SERVICES = {
    "goliath-bot": 1536,   # 1.5 GB
    "goliath-web": 512,    # 512 MB
}

# Project list (for completeness checks)
PROJECTS = [
    "union-ridge", "duff", "salt-branch", "blackford",
    "delta-bobcat", "tehuacana", "three-rivers", "scioto-ridge",
    "mayes", "graceland", "pecan-prairie", "duffy-bess",
]

# Telegram message length limit
MAX_MSG_LEN = 4000


# ======================================================================
# Individual health checks
# ======================================================================

def check_service_health() -> dict:
    """Check systemd service status for goliath-bot and goliath-web.

    Returns a dict with status for each service including:
      - active state (running/stopped/failed)
      - memory usage vs limit
      - restart count
      - uptime
    """
    results = {}
    issues = []

    for service_name, mem_limit_mb in SERVICES.items():
        svc = {"name": service_name, "status": "unknown", "detail": ""}

        # Check if active
        try:
            active = subprocess.run(
                ["systemctl", "is-active", f"{service_name}.service"],
                capture_output=True, text=True, timeout=10
            )
            state = active.stdout.strip()
            svc["active"] = state
        except (subprocess.TimeoutExpired, FileNotFoundError):
            state = "unknown"
            svc["active"] = state

        # Get service properties (memory, restart count, uptime)
        try:
            show = subprocess.run(
                ["systemctl", "show", f"{service_name}.service",
                 "--property=MemoryCurrent,NRestarts,ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=10
            )
            props = {}
            for line in show.stdout.strip().split("\n"):
                if "=" in line:
                    key, val = line.split("=", 1)
                    props[key.strip()] = val.strip()

            # Memory usage
            mem_bytes = props.get("MemoryCurrent", "")
            if mem_bytes and mem_bytes != "[not set]" and mem_bytes.isdigit():
                mem_mb = int(mem_bytes) / (1024 * 1024)
                svc["memory_mb"] = round(mem_mb, 1)
                mem_pct = (mem_mb / mem_limit_mb) * 100
                svc["memory_pct"] = round(mem_pct, 1)
                if mem_pct > 90:
                    issues.append(
                        f"{service_name}: Memory at {mem_pct:.0f}% of {mem_limit_mb}MB limit"
                    )

            # Restart count
            restarts = props.get("NRestarts", "0")
            if restarts.isdigit():
                svc["restarts"] = int(restarts)

            # Uptime
            ts_str = props.get("ActiveEnterTimestamp", "")
            if ts_str and ts_str != "n/a":
                try:
                    # systemd timestamp format: "Day YYYY-MM-DD HH:MM:SS TZ"
                    # Remove the day name prefix for parsing
                    parts = ts_str.split()
                    if len(parts) >= 3:
                        # Try parsing the timestamp portion
                        dt_str = " ".join(parts[:3])  # e.g., "Fri 2026-02-28 10:00:00"
                        start_dt = datetime.strptime(dt_str, "%a %Y-%m-%d %H:%M:%S")
                        start_dt = start_dt.replace(tzinfo=CT)
                        uptime = datetime.now(CT) - start_dt
                        days = uptime.days
                        hours = uptime.seconds // 3600
                        if days > 0:
                            svc["uptime"] = f"{days}d {hours}h"
                        else:
                            svc["uptime"] = f"{hours}h {(uptime.seconds % 3600) // 60}m"
                except (ValueError, IndexError):
                    pass

        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        # Determine status
        if state == "active":
            svc["status"] = "ok"
            detail_parts = []
            if "uptime" in svc:
                detail_parts.append(f"uptime: {svc['uptime']}")
            if "memory_mb" in svc:
                detail_parts.append(f"mem: {svc['memory_mb']}MB")
            if "restarts" in svc and svc["restarts"] > 0:
                detail_parts.append(f"restarts: {svc['restarts']}")
            svc["detail"] = f"Running ({', '.join(detail_parts)})" if detail_parts else "Running"
        elif state == "failed":
            svc["status"] = "critical"
            svc["detail"] = "Service failed"
            issues.append(f"{service_name}: Service has FAILED")
        elif state == "inactive":
            svc["status"] = "warning"
            svc["detail"] = "Service stopped"
            issues.append(f"{service_name}: Service is stopped")
        else:
            svc["status"] = "warning"
            svc["detail"] = f"State: {state}"
            issues.append(f"{service_name}: Unexpected state '{state}'")

        results[service_name] = svc

    # Overall service status
    statuses = [s["status"] for s in results.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "status": overall,
        "detail": "; ".join(s["detail"] for s in results.values()),
        "services": results,
        "issues": issues,
    }


def check_scheduler_health() -> dict:
    """Check scheduler health by examining database freshness and webhook server.

    Indicators:
      - memory.db modification time (should be within last hour during business hours)
      - webhook server responds on port 8000
    """
    issues = []
    now = datetime.now(CT)

    # Check memory.db freshness
    memory_db = DATA_DIR / "memory.db"
    db_fresh = True
    if memory_db.exists():
        mtime = datetime.fromtimestamp(memory_db.stat().st_mtime, tz=CT)
        age_hours = (now - mtime).total_seconds() / 3600
        # Only flag if during business hours (6 AM - 10 PM) and older than 1 hour
        if 6 <= now.hour <= 22 and age_hours > 1:
            db_fresh = False
            issues.append(
                f"memory.db not written to in {age_hours:.1f} hours "
                f"(last write: {mtime.strftime('%I:%M %p')})"
            )
    else:
        db_fresh = False
        issues.append("memory.db not found")

    # Check webhook server health (port 8000)
    webhook_ok = True
    try:
        import requests
        resp = requests.get("http://localhost:8000/webhook/health", timeout=5)
        if resp.status_code != 200:
            webhook_ok = False
            issues.append(f"Webhook server unhealthy (status {resp.status_code})")
    except Exception:
        webhook_ok = False
        issues.append("Webhook server not responding on port 8000")

    if not db_fresh or not webhook_ok:
        status = "warning"
    else:
        status = "ok"

    detail_parts = []
    if db_fresh:
        detail_parts.append("DB writes active")
    if webhook_ok:
        detail_parts.append("webhook OK")

    return {
        "status": status,
        "detail": ", ".join(detail_parts) if detail_parts else "Degraded",
        "issues": issues,
    }


def check_cron_jobs() -> dict:
    """Check if expected cron job output files exist for today.

    Expected files:
      - YYYY-MM-DD_daily_scan.md (after 11 PM)
      - YYYY-MM-DD_folder_cleanup.txt (after 7 PM)
    """
    issues = []
    now = datetime.now(CT)
    today = now.strftime("%Y-%m-%d")

    checks = {}

    # Daily scan report (expected after 11 PM)
    scan_report = CRON_REPORTS_DIR / f"{today}_daily_scan.md"
    if now.hour >= 23:
        if scan_report.exists():
            size = scan_report.stat().st_size
            checks["daily_scan"] = {"status": "ok", "detail": f"Generated ({size // 1024}KB)"}
        else:
            checks["daily_scan"] = {"status": "warning", "detail": "Missing (expected after 11 PM)"}
            issues.append(f"Daily scan report not found: {today}_daily_scan.md")
    else:
        checks["daily_scan"] = {"status": "ok", "detail": "Not yet due today"}

    # Folder cleanup report (expected after 7 PM, Mon-Fri and Sunday)
    cleanup_report = CRON_REPORTS_DIR / f"{today}_folder_cleanup.txt"
    weekday = now.weekday()  # 0=Mon ... 6=Sun
    is_cleanup_day = weekday != 5  # Every day except Saturday
    if is_cleanup_day and now.hour >= 19:
        if cleanup_report.exists():
            size = cleanup_report.stat().st_size
            checks["folder_cleanup"] = {"status": "ok", "detail": f"Generated ({size // 1024}KB)"}
        else:
            checks["folder_cleanup"] = {"status": "warning", "detail": "Missing (expected after 7 PM)"}
            issues.append(f"Folder cleanup report not found: {today}_folder_cleanup.txt")
    else:
        checks["folder_cleanup"] = {"status": "ok", "detail": "Not yet due today"}

    # Overall status
    statuses = [c["status"] for c in checks.values()]
    overall = "warning" if "warning" in statuses else "ok"

    return {
        "status": overall,
        "detail": "; ".join(f"{k}: {v['detail']}" for k, v in checks.items()),
        "checks": checks,
        "issues": issues,
    }


def check_databases() -> dict:
    """Check SQLite database health: size, corruption, and accessibility.

    Checks:
      - Each database can be opened
      - Quick integrity check (pragma integrity_check)
      - Size warning if > 500MB
    """
    issues = []
    db_details = {}

    for db_name, db_path in DATABASES.items():
        if not db_path.exists():
            db_details[db_name] = {"status": "warning", "detail": "File not found"}
            issues.append(f"Database not found: {db_name}")
            continue

        size_mb = db_path.stat().st_size / (1024 * 1024)
        detail_parts = [f"{size_mb:.1f}MB"]

        # Size warning
        if size_mb > 500:
            issues.append(f"{db_name} is {size_mb:.0f}MB (over 500MB limit)")
            status = "warning"
        else:
            status = "ok"

        # Integrity check (quick mode)
        try:
            conn = sqlite3.connect(str(db_path), timeout=10)
            cursor = conn.execute("PRAGMA integrity_check(1)")
            result = cursor.fetchone()[0]
            conn.close()
            if result != "ok":
                status = "critical"
                detail_parts.append("CORRUPT")
                issues.append(f"{db_name} integrity check failed: {result}")
            else:
                detail_parts.append("integrity OK")
        except sqlite3.Error as e:
            status = "critical"
            detail_parts.append(f"error: {e}")
            issues.append(f"{db_name} cannot be opened: {e}")
        except Exception as e:
            status = "warning"
            detail_parts.append(f"check failed: {e}")

        db_details[db_name] = {"status": status, "detail": ", ".join(detail_parts)}

    # Overall
    statuses = [d["status"] for d in db_details.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    summary = "; ".join(f"{k}: {v['detail']}" for k, v in db_details.items())
    return {
        "status": overall,
        "detail": summary,
        "databases": db_details,
        "issues": issues,
    }


def check_disk() -> dict:
    """Check disk usage on /opt/goliath partition."""
    issues = []

    try:
        usage = shutil.disk_usage(str(REPO_ROOT))
        total_gb = usage.total / (1024 ** 3)
        used_gb = usage.used / (1024 ** 3)
        free_gb = usage.free / (1024 ** 3)
        used_pct = (usage.used / usage.total) * 100

        detail = f"{used_pct:.0f}% used ({free_gb:.1f}GB free of {total_gb:.1f}GB)"

        if used_pct > 90:
            status = "critical"
            issues.append(f"Disk critically full: {used_pct:.0f}% used, only {free_gb:.1f}GB free")
        elif used_pct > 80:
            status = "warning"
            issues.append(f"Disk usage high: {used_pct:.0f}% used, {free_gb:.1f}GB free")
        else:
            status = "ok"

        return {
            "status": status,
            "detail": detail,
            "used_pct": round(used_pct, 1),
            "free_gb": round(free_gb, 1),
            "total_gb": round(total_gb, 1),
            "issues": issues,
        }
    except Exception as e:
        return {
            "status": "warning",
            "detail": f"Cannot check disk: {e}",
            "issues": [f"Disk check failed: {e}"],
        }


def check_network() -> dict:
    """Check network health: web platform, Cloudflare tunnel, credentials.

    Checks:
      - Web platform API responds on localhost (port 80 or 3001)
      - cloudflared process is running
      - Gmail IMAP credentials are configured in environment
    """
    issues = []
    checks = {}

    # Web platform health check
    web_ok = False
    web_port = None
    for port in [80, 3001]:
        try:
            import requests
            resp = requests.get(f"http://localhost:{port}/api/health", timeout=5)
            if resp.status_code == 200:
                web_ok = True
                web_port = port
                break
        except Exception:
            continue

    if web_ok:
        checks["web_platform"] = {"status": "ok", "detail": f"Responding on port {web_port}"}
    else:
        checks["web_platform"] = {"status": "warning", "detail": "Not responding on port 80 or 3001"}
        issues.append("Web platform not responding")

    # Cloudflare tunnel check
    try:
        result = subprocess.run(
            ["pgrep", "-f", "cloudflared"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            checks["cloudflare_tunnel"] = {"status": "ok", "detail": "Process running"}
        else:
            checks["cloudflare_tunnel"] = {"status": "critical", "detail": "Process not found"}
            issues.append("Cloudflare tunnel not running -- web UI unreachable externally")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        checks["cloudflare_tunnel"] = {"status": "warning", "detail": "Cannot check (pgrep not available)"}

    # Gmail IMAP credentials check
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "") or os.environ.get("GOLIATH_GMAIL", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "") or os.environ.get("GOLIATH_GMAIL_APP_PASSWORD", "")
    if gmail_addr and gmail_pass:
        checks["gmail_credentials"] = {"status": "ok", "detail": f"Configured ({gmail_addr})"}
    else:
        missing = []
        if not gmail_addr:
            missing.append("GMAIL_ADDRESS")
        if not gmail_pass:
            missing.append("GMAIL_APP_PASSWORD")
        checks["gmail_credentials"] = {
            "status": "warning",
            "detail": f"Missing: {', '.join(missing)}"
        }
        issues.append(f"Gmail credentials incomplete: {', '.join(missing)}")

    # Overall
    statuses = [c["status"] for c in checks.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "status": overall,
        "detail": "; ".join(f"{k}: {v['detail']}" for k, v in checks.items()),
        "checks": checks,
        "issues": issues,
    }


# ======================================================================
# Main health check runner
# ======================================================================

def run_health_check() -> dict:
    """Run all health checks and return a structured result.

    Returns:
        Dict with keys: timestamp, overall_status, checks, issues, recommendations.
    """
    now = datetime.now(CT)

    # Run all checks
    bot_web = check_service_health()
    scheduler = check_scheduler_health()
    cron_jobs = check_cron_jobs()
    databases = check_databases()
    disk = check_disk()
    network = check_network()

    # Collect all issues
    all_issues = []
    all_issues.extend(bot_web.get("issues", []))
    all_issues.extend(scheduler.get("issues", []))
    all_issues.extend(cron_jobs.get("issues", []))
    all_issues.extend(databases.get("issues", []))
    all_issues.extend(disk.get("issues", []))
    all_issues.extend(network.get("issues", []))

    # Build recommendations based on issues
    recommendations = _build_recommendations(all_issues, bot_web, network)

    # Determine overall status
    all_statuses = [
        bot_web["status"], scheduler["status"], cron_jobs["status"],
        databases["status"], disk["status"], network["status"],
    ]

    if "critical" in all_statuses:
        overall = "critical"
    elif "warning" in all_statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "timestamp": now.isoformat(),
        "overall_status": overall,
        "checks": {
            "bot_service": {
                "status": bot_web.get("services", {}).get("goliath-bot", {}).get("status", "unknown"),
                "detail": bot_web.get("services", {}).get("goliath-bot", {}).get("detail", "Unknown"),
            },
            "web_service": {
                "status": bot_web.get("services", {}).get("goliath-web", {}).get("status", "unknown"),
                "detail": bot_web.get("services", {}).get("goliath-web", {}).get("detail", "Unknown"),
            },
            "scheduler": {
                "status": scheduler["status"],
                "detail": scheduler["detail"],
            },
            "databases": {
                "status": databases["status"],
                "detail": databases["detail"],
            },
            "disk": {
                "status": disk["status"],
                "detail": disk["detail"],
            },
            "network": {
                "status": network["status"],
                "detail": network["detail"],
            },
            "cron_jobs": {
                "status": cron_jobs["status"],
                "detail": cron_jobs["detail"],
            },
        },
        "issues": all_issues,
        "recommendations": recommendations,
        # Preserve raw data for programmatic access
        "_raw": {
            "services": bot_web,
            "scheduler": scheduler,
            "cron_jobs": cron_jobs,
            "databases": databases,
            "disk": disk,
            "network": network,
        },
    }


def _build_recommendations(issues: list[str], services: dict, network: dict) -> list[str]:
    """Generate actionable recommendations based on detected issues."""
    recs = []

    for issue in issues:
        issue_lower = issue.lower()

        if "failed" in issue_lower and "service" in issue_lower:
            svc = "goliath-bot" if "bot" in issue_lower else "goliath-web"
            recs.append(f"Restart service: sudo systemctl restart {svc}")
            recs.append(f"Check logs: journalctl -u {svc} --since '1 hour ago' --no-pager")

        elif "stopped" in issue_lower and "service" in issue_lower:
            svc = "goliath-bot" if "bot" in issue_lower else "goliath-web"
            recs.append(f"Start service: sudo systemctl start {svc}")

        elif "memory" in issue_lower and "limit" in issue_lower:
            recs.append("Consider restarting the service to free memory")

        elif "cloudflare" in issue_lower:
            recs.append("Restart tunnel: cloudflared tunnel run goliath")
            recs.append("Check tunnel status: cloudflared tunnel info goliath")

        elif "daily scan" in issue_lower and "not found" in issue_lower:
            recs.append("Check daily_scan.py logs: journalctl -u goliath-bot --since '23:00' --no-pager")

        elif "folder cleanup" in issue_lower:
            recs.append("Check folder_cleanup logs: journalctl -u goliath-bot --since '19:00' --no-pager")

        elif "integrity" in issue_lower or "corrupt" in issue_lower:
            recs.append("Database may need repair: sqlite3 <db> 'PRAGMA integrity_check'")
            recs.append("Consider restoring from backup if corruption is confirmed")

        elif "disk" in issue_lower and ("full" in issue_lower or "high" in issue_lower):
            recs.append("Clean up old reports: ls -la /opt/goliath/cron-jobs/reports/")
            recs.append("Check large files: du -sh /opt/goliath/*/ | sort -rh | head")

        elif "webhook" in issue_lower:
            recs.append("Check if bot is running: systemctl status goliath-bot")
            recs.append("Webhook server starts with the bot on port 8000")

        elif "gmail" in issue_lower:
            recs.append("Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in /opt/goliath/.env")

    # Deduplicate
    seen = set()
    unique_recs = []
    for r in recs:
        if r not in seen:
            seen.add(r)
            unique_recs.append(r)

    return unique_recs


# ======================================================================
# Telegram formatting and sending
# ======================================================================

STATUS_ICONS = {
    "ok": "✅",
    "warning": "⚠️",
    "critical": "❌",
    "unknown": "❓",
}


def format_telegram_message(result: dict) -> str:
    """Format the health check result as a clean HTML Telegram message."""
    lines = []

    # Header with overall status
    overall = result["overall_status"]
    if overall == "healthy":
        header_icon = "✅"
    elif overall == "degraded":
        header_icon = "⚠️"
    else:
        header_icon = "❌"

    lines.append(f"<b>{header_icon} GOLIATH Health Check</b>")
    lines.append(f"<i>{datetime.now(CT).strftime('%Y-%m-%d %I:%M %p CT')}</i>")
    lines.append("")

    # Individual check results
    check_labels = {
        "bot_service": "Bot Service",
        "web_service": "Web Platform",
        "scheduler": "Scheduler",
        "cron_jobs": "Cron Jobs",
        "databases": "Databases",
        "disk": "Disk",
        "network": "Network",
    }

    for key, label in check_labels.items():
        check = result["checks"].get(key, {})
        status = check.get("status", "unknown")
        detail = check.get("detail", "No data")
        icon = STATUS_ICONS.get(status, "❓")
        # Truncate detail if too long
        if len(detail) > 120:
            detail = detail[:117] + "..."
        lines.append(f"{icon} <b>{label}</b> -- {_html_escape(detail)}")

    # Issues section
    if result["issues"]:
        lines.append("")
        lines.append("<b>Issues:</b>")
        for issue in result["issues"][:10]:  # Cap at 10 issues
            lines.append(f"  - {_html_escape(issue)}")

    # Recommendations section
    if result["recommendations"]:
        lines.append("")
        lines.append("<b>Recommendations:</b>")
        for rec in result["recommendations"][:8]:  # Cap at 8 recommendations
            lines.append(f"  - <code>{_html_escape(rec)}</code>")

    return "\n".join(lines)


def _html_escape(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
                # Fallback without HTML
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
    """Run all health checks and report results to Telegram."""
    print(f"[{datetime.now(CT).isoformat()}] Running GOLIATH health check...")

    result = run_health_check()

    # Print summary to stdout
    print(f"Overall status: {result['overall_status']}")
    for key, check in result["checks"].items():
        print(f"  {key}: {check['status']} -- {check['detail']}")

    if result["issues"]:
        print(f"\nIssues ({len(result['issues'])}):")
        for issue in result["issues"]:
            print(f"  - {issue}")

    if result["recommendations"]:
        print(f"\nRecommendations ({len(result['recommendations'])}):")
        for rec in result["recommendations"]:
            print(f"  - {rec}")

    # Send to Telegram if there are issues or if run explicitly
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\nTelegram credentials not configured, skipping notification.")
        return 0

    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()
    msg = format_telegram_message(result)
    success = send_telegram_message(chat_id, msg)

    if success:
        print("\nHealth check report sent to Telegram.")
    else:
        print("\nFailed to send health check report to Telegram.")

    return 0 if result["overall_status"] == "healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
