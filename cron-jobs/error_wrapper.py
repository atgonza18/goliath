#!/usr/bin/env python3
"""
Error Wrapper — Standardized error handling for GOLIATH cron jobs.

Provides a decorator that wraps cron job main functions with:
  - Clean, human-readable Telegram error messages (no raw tracebacks)
  - Auto-retry on transient errors (network timeouts, IMAP connection drops)
  - Full traceback logging to file for debugging
  - Structured error reporting with script name, timestamp, and summary

Usage:
    from error_wrapper import safe_run

    @safe_run(script_name="daily_scan", retries=1)
    def main():
        # existing code
        pass

    if __name__ == "__main__":
        main()

Or as a context manager for finer-grained control:

    from error_wrapper import SafeExecution

    with SafeExecution("daily_scan", retries=1):
        do_something()
"""

import functools
import logging
import os
import socket
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

CT = ZoneInfo("America/Chicago")

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("REPORT_CHAT_ID", "") or os.environ.get("ALLOWED_CHAT_IDS", "")

# Error log directory — all full tracebacks go here for debugging
ERROR_LOG_DIR = REPO_ROOT / "cron-jobs" / "logs"

# Transient error types that warrant an automatic retry
TRANSIENT_ERRORS = (
    ConnectionError,
    ConnectionResetError,
    ConnectionRefusedError,
    TimeoutError,
    socket.timeout,
    socket.gaierror,
    OSError,  # Covers "Network is unreachable", "Connection refused", etc.
)

# Additional string patterns in error messages that indicate transient failures
TRANSIENT_PATTERNS = [
    "IMAP",
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
    "temporary failure",
    "network is unreachable",
    "name resolution",
    "ssl",
    "EOF occurred",
    "broken pipe",
]

logger = logging.getLogger(__name__)


def _is_transient(exc: Exception) -> bool:
    """Determine if an exception is transient and worth retrying."""
    if isinstance(exc, TRANSIENT_ERRORS):
        return True
    # Check error message for transient patterns
    error_msg = str(exc).lower()
    return any(pattern in error_msg for pattern in TRANSIENT_PATTERNS)


def _format_short_traceback(exc: Exception, limit: int = 5) -> str:
    """Extract the last N lines of a traceback for a compact error display."""
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    # Flatten into individual lines
    all_lines = []
    for line in tb_lines:
        all_lines.extend(line.rstrip().split("\n"))
    # Return last `limit` lines (the most relevant part)
    relevant = all_lines[-limit:]
    return "\n".join(relevant)


def _one_line_summary(exc: Exception) -> str:
    """Produce a single-line human-readable error summary."""
    exc_type = type(exc).__name__
    exc_msg = str(exc)
    if len(exc_msg) > 200:
        exc_msg = exc_msg[:197] + "..."
    return f"{exc_type}: {exc_msg}"


def _log_traceback_to_file(script_name: str, exc: Exception) -> Path | None:
    """Write the full traceback to a timestamped log file. Returns the log path."""
    try:
        ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.now(CT)
        log_filename = f"{now.strftime('%Y-%m-%d_%H%M%S')}_{script_name}.log"
        log_path = ERROR_LOG_DIR / log_filename
        full_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log_path.write_text(
            f"Script: {script_name}\n"
            f"Timestamp: {now.isoformat()}\n"
            f"Error: {_one_line_summary(exc)}\n"
            f"\n--- Full Traceback ---\n\n"
            f"{full_tb}"
        )
        return log_path
    except Exception as log_err:
        logger.warning(f"Failed to write error log: {log_err}")
        return None


def _html_escape(text: str) -> str:
    """Escape HTML special characters for Telegram."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_error_telegram(script_name: str, exc: Exception, attempt: int, max_attempts: int,
                          log_path: Path | None = None) -> bool:
    """Send a clean, formatted error notification to Telegram.

    Returns True if the message was sent successfully.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Cannot send Telegram error notification: missing credentials")
        return False

    import requests

    chat_id = TELEGRAM_CHAT_ID.split(",")[0].strip()
    now = datetime.now(CT)
    timestamp = now.strftime("%Y-%m-%d %I:%M %p CT")

    summary = _html_escape(_one_line_summary(exc))
    short_tb = _html_escape(_format_short_traceback(exc))

    retry_note = ""
    if max_attempts > 1:
        if attempt < max_attempts:
            retry_note = f"\n\nRetrying (attempt {attempt + 1}/{max_attempts})..."
        else:
            retry_note = f"\n\nAll {max_attempts} attempts failed."

    log_note = ""
    if log_path:
        rel_path = log_path.relative_to(REPO_ROOT) if log_path.is_relative_to(REPO_ROOT) else log_path
        log_note = f"\n\nFull traceback: <code>{rel_path}</code>"

    text = (
        f"<b>GOLIATH Script Error</b>\n"
        f"<i>{timestamp}</i>\n\n"
        f"<b>Script:</b> {_html_escape(script_name)}\n"
        f"<b>Error:</b> {summary}\n\n"
        f"<b>Traceback (last 5 lines):</b>\n"
        f"<pre>{short_tb}</pre>"
        f"{retry_note}"
        f"{log_note}"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            # Fallback without HTML parse mode
            payload.pop("parse_mode")
            payload["text"] = (
                f"GOLIATH Script Error\n{timestamp}\n\n"
                f"Script: {script_name}\n"
                f"Error: {_one_line_summary(exc)}\n\n"
                f"Traceback (last 5 lines):\n{_format_short_traceback(exc)}"
            )
            resp = requests.post(url, json=payload, timeout=30)
        return resp.status_code == 200
    except Exception as send_err:
        print(f"Failed to send Telegram error notification: {send_err}")
        return False


def safe_run(script_name: str, retries: int = 0):
    """Decorator that wraps a function with standardized error handling.

    Args:
        script_name: Human-readable name for the script (used in error messages).
        retries: Number of retry attempts for transient errors (0 = no retries).
                 Non-transient errors are never retried.

    Example:
        @safe_run(script_name="daily_scan", retries=1)
        def main():
            # ... your script code ...
            pass
    """
    max_attempts = 1 + retries

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None

            for attempt in range(max_attempts):
                try:
                    result = func(*args, **kwargs)
                    return result
                except SystemExit as e:
                    # Let explicit sys.exit() calls through
                    raise
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    last_exc = exc
                    is_transient = _is_transient(exc)

                    # Log the full traceback to file
                    log_path = _log_traceback_to_file(script_name, exc)

                    # Print to stderr for journalctl/systemd visibility
                    print(
                        f"[{datetime.now(CT).isoformat()}] {script_name} failed "
                        f"(attempt {attempt + 1}/{max_attempts}): {_one_line_summary(exc)}",
                        file=sys.stderr,
                    )

                    # If transient and we have retries left, retry silently
                    if is_transient and attempt < max_attempts - 1:
                        import time
                        wait = 2 ** attempt * 5  # 5s, 10s, 20s, ...
                        print(
                            f"  Transient error, retrying in {wait}s...",
                            file=sys.stderr,
                        )
                        time.sleep(wait)
                        continue

                    # Final failure — send clean Telegram notification
                    _send_error_telegram(
                        script_name, exc, attempt, max_attempts, log_path
                    )

                    # Non-transient errors should not be retried
                    if not is_transient:
                        return 1

            # All retries exhausted
            if last_exc:
                return 1
            return 0

        return wrapper
    return decorator


class SafeExecution:
    """Context manager for wrapping code blocks with error handling.

    Usage:
        with SafeExecution("my_operation", retries=1):
            do_something()
    """

    def __init__(self, script_name: str, retries: int = 0):
        self.script_name = script_name
        self.retries = retries
        self.max_attempts = 1 + retries

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            return False  # No exception, proceed normally

        if exc_type in (SystemExit, KeyboardInterrupt):
            return False  # Let these through

        # Log to file
        log_path = _log_traceback_to_file(self.script_name, exc_val)

        print(
            f"[{datetime.now(CT).isoformat()}] {self.script_name} failed: "
            f"{_one_line_summary(exc_val)}",
            file=sys.stderr,
        )

        # Send Telegram notification
        _send_error_telegram(
            self.script_name, exc_val, self.max_attempts - 1,
            self.max_attempts, log_path
        )

        # Suppress the exception (return True) so the script exits cleanly
        return True
