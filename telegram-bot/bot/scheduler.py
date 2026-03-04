"""
Async Background Scheduler — runs cron-style tasks inside the bot's event loop.

Replaces system crontab with a lightweight asyncio-based scheduler that:
  - Runs tasks at specific times in America/Chicago timezone
  - Supports day-of-week filtering (e.g. Mon-Fri only, Sunday only)
  - Survives individual task failures without crashing
  - Provides a registry of scheduled tasks for introspection
  - Prevents double-firing via in-flight guards and eager last_run stamping

Race-condition prevention (the three-layer defense):
  1. In-flight set: each task name is added to _in_flight BEFORE execution and
     removed AFTER completion. _next_due_task() skips tasks that are in-flight.
  2. Eager last_run: last_run is stamped BEFORE the callback runs, so the
     "ran less than 2 min ago" guard in _next_due_task() fires immediately.
  3. Proactive dedup: proactive sessions have their own date-based dedup dict
     as an extra safety net (one run per session type per calendar day).

Day-of-week schedule:
  Monday–Friday (workdays):
    - 12:05 AM CT — Create daily constraints folder (daily — runs every day)
    - 5:00 AM CT  — Morning report (Bible verse + todo list + scan + trends)
    - 6:00 AM CT  — Morning proactive thinking session
    - 5:00 PM CT  — Daily Proactive Follow-Up PDF report (end-of-day, what to chase tomorrow)
    - 6:00 PM CT  — Evening proactive thinking session
    - 7:00 PM CT  — Folder cleanup scan
    - 11:00 PM CT — Daily scan (runs every day)
    [DISABLED] Follow-up queue scans (10 AM, 4 PM) — replaced by consolidated PDF
  Saturday: Day off — only infrastructure tasks (email poll, etc.)
  Sunday:   Shifted schedule — reports/sessions fire at 4 PM CT
            (respects church in the morning, preps for Monday evening)
    - 4:00 PM CT  — Morning report (Sunday edition)
    - 4:00 PM CT  — Morning proactive thinking session (Sunday edition)
    - 4:15 PM CT  — Daily Proactive Follow-Up PDF report (Sunday edition)
    - 6:00 PM CT  — Evening proactive thinking session (Sunday — same time)
    - 7:00 PM CT  — Folder cleanup scan (Sunday — same time)
    [DISABLED] Follow-up queue scan (5 PM Sunday) — replaced by consolidated PDF

  Always-on tasks (every day, including Saturday):
    - Every 45s   — Poll Gmail IMAP for inbound [INBOX:] tagged emails
    - 12:05 AM CT — Create daily constraints folder
    - 11:00 PM CT — Daily scan (run Claude analysis of all 12 projects)
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Awaitable, Optional
from zoneinfo import ZoneInfo

from bot.config import (
    REPO_ROOT, REPORT_CHAT_ID, TELEGRAM_BOT_TOKEN, PROJECTS,
    PROACTIVE_FOLLOWUP_DB_PATH,
    ESCALATION_DB_PATH, ESCALATION_SCAN_TIMES,   # backward compat aliases
    FOLLOWUP_DB_PATH, FOLLOWUP_SCAN_TIMES,
    MEMORY_DB_PATH,
)

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

# Day-of-week sets (weekday(): 0=Monday, 1=Tuesday, ..., 6=Sunday)
MON_FRI = {0, 1, 2, 3, 4}   # Monday through Friday — normal workday schedule
SUN_ONLY = {6}               # Sunday only — shifted schedule (4 PM CT start)

# Paths
CRON_JOBS_DIR = REPO_ROOT / "cron-jobs"
CRON_REPORTS_DIR = CRON_JOBS_DIR / "reports"
REPORTS_DIR = REPO_ROOT / "reports"


@dataclass
class ScheduledTask:
    """A single scheduled task definition.

    Fields:
        name:        Unique identifier (used as in-flight guard key).
        hour/minute: Target fire time in CT timezone (24-hour clock).
                     Set both to -1 for interval-based tasks.
        callback:    Async callable — receives the Scheduler instance.
        interval_seconds: If set (> 0), task fires repeatedly at this interval
                     instead of at a fixed daily time. hour/minute are ignored.
        days_of_week: Optional set of weekday numbers (0=Mon ... 6=Sun) on which
                     this task is allowed to fire. None means every day.
                     Only applies to daily (non-interval) tasks.
        last_run:    Timestamp of the most recent *start* of this task
                     (set eagerly, before callback runs, to prevent re-trigger).
        last_status: "ok" | "error" | None — outcome of the most recent run.
        last_error:  Truncated error message if last_status == "error".
        enabled:     Set False to skip without unregistering.
    """
    name: str
    hour: int              # 0-23 in CT (ignored for interval tasks)
    minute: int            # 0-59 (ignored for interval tasks)
    callback: Callable[..., Awaitable[None]]
    description: str = ""
    interval_seconds: int = 0  # 0 = daily task, >0 = repeating interval
    days_of_week: Optional[set[int]] = None  # None = every day; {0,1,2,3,4} = Mon-Fri
    last_run: Optional[datetime] = None
    last_status: Optional[str] = None   # "ok", "error"
    last_error: Optional[str] = None
    enabled: bool = True


class Scheduler:
    """Lightweight async scheduler that fires tasks at specified times (CT timezone).

    Thread-safety note: this scheduler runs entirely within a single asyncio
    event loop, so there are no threading concerns. The in-flight guard protects
    against the scheduler loop re-entering a task that is still awaited from a
    previous iteration (e.g. if sleep(61) elapses before a long-running task
    completes — which CAN happen because _fire_task is awaited inline and the
    sleep only runs after it returns, but the _next_due_task check can still
    see a stale last_run if it was not stamped eagerly).
    """

    def __init__(self, bot=None):
        self._tasks: list[ScheduledTask] = []
        self._running = False
        self._task_handle: Optional[asyncio.Task] = None
        self.bot = bot  # telegram Bot instance for sending messages
        # In-flight guard: set of task names currently executing.
        # Checked by _next_due_task() and _fire_task() to prevent double-firing.
        self._in_flight: set[str] = set()

    # ------------------------------------------------------------------
    # Task registration
    # ------------------------------------------------------------------

    def add_task(
        self,
        name: str,
        hour: int,
        minute: int,
        callback: Callable[..., Awaitable[None]],
        description: str = "",
        days_of_week: Optional[set[int]] = None,
    ) -> None:
        """Register a new daily task.

        Args:
            days_of_week: Optional set of weekday numbers (0=Mon .. 6=Sun).
                          If None, the task fires every day.
        """
        task = ScheduledTask(
            name=name,
            hour=hour,
            minute=minute,
            callback=callback,
            description=description,
            days_of_week=days_of_week,
        )
        self._tasks.append(task)
        days_label = ""
        if days_of_week is not None:
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            days_label = f" [{', '.join(day_names[d] for d in sorted(days_of_week))}]"
        logger.info(
            f"Scheduler: registered '{name}' at {hour:02d}:{minute:02d} CT{days_label} — {description}"
        )

    def add_interval_task(
        self,
        name: str,
        interval_seconds: int,
        callback: Callable[..., Awaitable[None]],
        description: str = "",
    ) -> None:
        """Register a repeating interval task (fires every N seconds)."""
        task = ScheduledTask(
            name=name,
            hour=-1,
            minute=-1,
            callback=callback,
            interval_seconds=interval_seconds,
            description=description,
        )
        self._tasks.append(task)
        logger.info(
            f"Scheduler: registered interval task '{name}' every {interval_seconds}s — {description}"
        )

    def list_tasks(self) -> list[dict]:
        """Return a list of all scheduled tasks with their status."""
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        result = []
        for t in self._tasks:
            if t.interval_seconds > 0:
                time_str = f"every {t.interval_seconds}s"
            else:
                time_str = f"{t.hour:02d}:{t.minute:02d}"
            days_str = None
            if t.days_of_week is not None:
                days_str = ", ".join(day_names[d] for d in sorted(t.days_of_week))
            result.append({
                "name": t.name,
                "time_ct": time_str,
                "days": days_str,
                "description": t.description,
                "enabled": t.enabled,
                "in_flight": t.name in self._in_flight,
                "last_run": t.last_run.isoformat() if t.last_run else None,
                "last_status": t.last_status,
                "last_error": t.last_error,
            })
        return result

    def format_task_list_html(self) -> str:
        """Format the task list as an HTML string for Telegram."""
        if not self._tasks:
            return "<i>No scheduled tasks.</i>"

        now_ct = datetime.now(CT)
        lines = [f"<b>Scheduled Tasks</b>  (now: {now_ct.strftime('%I:%M %p CT')})\n"]

        for t in self._tasks:
            # Status icon: R = running, + = enabled, - = disabled
            if t.name in self._in_flight:
                status_icon = "R"
            elif t.enabled:
                status_icon = "+"
            else:
                status_icon = "-"

            if t.interval_seconds > 0:
                mins = t.interval_seconds // 60
                time_str = f"every {mins}m" if mins >= 1 else f"every {t.interval_seconds}s"
            else:
                time_str = f"{t.hour:02d}:{t.minute:02d} CT"
                if t.days_of_week is not None:
                    day_names = ["M", "Tu", "W", "Th", "F", "Sa", "Su"]
                    time_str += f" ({','.join(day_names[d] for d in sorted(t.days_of_week))})"
            last = ""
            if t.last_run:
                last = f" | last: {t.last_run.strftime('%m/%d %I:%M %p')} [{t.last_status or 'running'}]"
            lines.append(
                f"<code>[{status_icon}]</code> <b>{t.name}</b> @ {time_str}{last}"
            )
            if t.description:
                lines.append(f"    <i>{t.description}</i>")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def start(self) -> asyncio.Task:
        """Launch the scheduler as a background asyncio task."""
        if self._running:
            logger.warning("Scheduler already running")
            return self._task_handle

        self._running = True
        self._task_handle = asyncio.create_task(self._run_loop())
        logger.info(f"Scheduler started with {len(self._tasks)} task(s)")
        return self._task_handle

    def stop(self) -> None:
        """Signal the scheduler to stop."""
        self._running = False
        if self._task_handle and not self._task_handle.done():
            self._task_handle.cancel()
        logger.info("Scheduler stopped")

    async def _run_loop(self) -> None:
        """Main loop: sleep until the next task fires, then run it.

        The loop wakes up every 60s (at most) to re-evaluate which task is
        next. When a task is due (seconds_until <= 0), it fires inline —
        meaning the loop blocks on that task. This is intentional: since
        _fire_task stamps last_run eagerly and uses the in-flight guard,
        even if we awaited the task concurrently, a re-entrant call would
        be safely skipped.

        After firing, a 61-second cooldown prevents the same minute from
        being re-evaluated. Combined with the 120-second last_run guard
        in _next_due_task, this provides ample protection.
        """
        # Wait a few seconds on startup so the bot is fully initialized
        await asyncio.sleep(5)
        logger.info("Scheduler loop active")

        while self._running:
            try:
                now_ct = datetime.now(CT)
                next_task, seconds_until = self._next_due_task(now_ct)

                if next_task is None or seconds_until is None:
                    # No eligible tasks (all disabled, in-flight, or none registered)
                    await asyncio.sleep(60)
                    continue

                # If a task is due right now (within 30s window), fire it
                if seconds_until <= 0:
                    await self._fire_task(next_task)
                    # Small cooldown so we don't re-fire the same minute
                    await asyncio.sleep(61)
                    continue

                # Sleep until the next task, but wake up periodically to recheck
                # (in case tasks are added/removed or clock drifts)
                sleep_time = min(seconds_until, 60)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                logger.info("Scheduler loop cancelled")
                break
            except Exception:
                logger.exception("Scheduler loop error — continuing in 60s")
                await asyncio.sleep(60)

    def _next_due_task(self, now_ct: datetime) -> tuple[Optional[ScheduledTask], Optional[float]]:
        """Find the next task that should fire, and how many seconds until it fires.

        Skips tasks that are:
          - disabled
          - currently in-flight (already executing)
          - ran less than 2 minutes ago for daily tasks (last_run is set eagerly)
          - ran less than interval_seconds ago for interval tasks
        """
        if not self._tasks:
            return None, None

        best_task = None
        best_seconds = None

        for task in self._tasks:
            if not task.enabled:
                continue

            # GUARD 1: Skip tasks that are already executing.
            # This is the primary defense against double-firing.
            if task.name in self._in_flight:
                continue

            # --- Interval-based tasks ---
            if task.interval_seconds > 0:
                if task.last_run:
                    last_run_ct = task.last_run.astimezone(CT) if task.last_run.tzinfo else task.last_run
                    elapsed = (now_ct - last_run_ct).total_seconds()
                    seconds = max(0, task.interval_seconds - elapsed)
                else:
                    # Never run before — fire immediately
                    seconds = 0

                if best_seconds is None or seconds < best_seconds:
                    best_task = task
                    best_seconds = seconds
                continue

            # --- Daily time-based tasks ---
            # Build the target time for today
            target_today = now_ct.replace(
                hour=task.hour, minute=task.minute, second=0, microsecond=0
            )

            # If already past today's target, schedule for tomorrow
            if now_ct >= target_today + timedelta(minutes=1):
                target = target_today + timedelta(days=1)
            else:
                target = target_today

            # GUARD 2: Skip if this task started recently (last_run is stamped
            # eagerly at task start, so this catches in-progress tasks even if
            # the in-flight set were somehow missed).
            if task.last_run:
                last_run_ct = task.last_run.astimezone(CT) if task.last_run.tzinfo else task.last_run
                if (now_ct - last_run_ct).total_seconds() < 120:
                    # Started less than 2 minutes ago — skip to tomorrow
                    target = target_today + timedelta(days=1)

            # GUARD 3: Day-of-week filter. If the task has a days_of_week
            # restriction, advance the target to the next allowed day.
            if task.days_of_week is not None:
                for _advance in range(7):
                    if target.weekday() in task.days_of_week:
                        break
                    target += timedelta(days=1)
                    # Reset to the task's scheduled time on the new day
                    target = target.replace(
                        hour=task.hour, minute=task.minute, second=0, microsecond=0
                    )
                else:
                    # No valid day found within 7 days — should never happen
                    # but skip this task to be safe
                    continue

            seconds = (target - now_ct).total_seconds()

            if best_seconds is None or seconds < best_seconds:
                best_task = task
                best_seconds = seconds

        return best_task, best_seconds

    async def _fire_task(self, task: ScheduledTask) -> None:
        """Execute a single scheduled task with error handling.

        Three-layer defense against double-firing:
          1. In-flight guard: if task.name is already in _in_flight, bail out.
          2. Eager last_run: stamp last_run BEFORE the callback runs, so the
             _next_due_task "ran < 2 min ago" check kicks in immediately.
          3. Task-level dedup: proactive tasks have their own date-based guard
             (see _run_proactive_task).

        last_status is set to "ok" or "error" only AFTER the callback completes.
        While the task is in-flight, last_status remains from the previous run
        (or None), but the in_flight indicator in list_tasks() shows "R".
        """
        # --- In-flight guard (layer 1) ---
        if task.name in self._in_flight:
            logger.warning(
                f"Scheduler: SKIPPING '{task.name}' — already in-flight "
                f"(this is the double-fire guard working correctly)"
            )
            return

        # --- Stamp last_run eagerly (layer 2) ---
        # This ensures _next_due_task() sees a recent last_run immediately,
        # even before the callback returns.
        task.last_run = datetime.now(CT)

        # --- Mark as in-flight ---
        self._in_flight.add(task.name)
        logger.info(f"Scheduler: firing task '{task.name}' (in-flight: {self._in_flight})")
        start = time.monotonic()

        try:
            await asyncio.wait_for(task.callback(self), timeout=900)  # 15 min max
            duration = time.monotonic() - start
            # Update last_run to the actual completion time (still prevents re-fire)
            task.last_run = datetime.now(CT)
            task.last_status = "ok"
            task.last_error = None
            logger.info(f"Scheduler: '{task.name}' completed in {duration:.1f}s")
        except asyncio.TimeoutError:
            duration = time.monotonic() - start
            task.last_run = datetime.now(CT)
            task.last_status = "error"
            task.last_error = f"Timed out after {duration:.0f}s"
            logger.error(f"Scheduler: '{task.name}' timed out after {duration:.1f}s")
        except Exception as e:
            duration = time.monotonic() - start
            task.last_run = datetime.now(CT)
            task.last_status = "error"
            task.last_error = str(e)[:500]
            logger.exception(f"Scheduler: '{task.name}' failed after {duration:.1f}s")
        finally:
            # --- Always clear the in-flight flag ---
            self._in_flight.discard(task.name)
            logger.debug(f"Scheduler: '{task.name}' removed from in-flight set")


# ======================================================================
# Task implementations
# ======================================================================

async def _send_telegram(bot, chat_id: int, text: str) -> None:
    """Send a message to Telegram, chunked if needed, with HTML fallback."""
    from bot.utils.formatting import chunk_message

    for chunk in chunk_message(text, max_len=4000):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            # Fallback: send without HTML parsing
            try:
                await bot.send_message(chat_id=chat_id, text=chunk)
            except Exception:
                logger.exception(f"Failed to send Telegram message to {chat_id}")


async def _send_telegram_document(bot, chat_id: int, file_path: Path, caption: str = "") -> bool:
    """Send a file as a Telegram document attachment.

    Uses the bot's native send_document method (python-telegram-bot 21.x).
    Falls back to raw HTTP multipart POST via requests if that fails.
    Returns True on success, False on failure.
    """
    if not file_path.exists():
        logger.warning(f"Cannot send document — file not found: {file_path}")
        return False

    # Try python-telegram-bot's native send_document first
    try:
        with open(file_path, "rb") as f:
            await bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=file_path.name,
                caption=caption[:1024] if caption else None,  # Telegram caption limit
                parse_mode="HTML" if caption else None,
            )
        logger.info(f"Sent document via bot.send_document: {file_path.name}")
        return True
    except Exception as e:
        logger.warning(f"bot.send_document failed for {file_path.name}: {e} — trying raw API fallback")

    # Fallback: raw requests POST to Telegram API
    try:
        import requests as req
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
        with open(file_path, "rb") as f:
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption[:1024]
                data["parse_mode"] = "HTML"
            files = {"document": (file_path.name, f)}
            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: req.post(url, data=data, files=files, timeout=60),
            )
        if resp.status_code == 200:
            logger.info(f"Sent document via raw API: {file_path.name}")
            return True
        else:
            logger.error(f"Raw API sendDocument failed ({resp.status_code}): {resp.text[:300]}")
            return False
    except Exception:
        logger.exception(f"Failed to send document {file_path.name} via both methods")
        return False


async def _run_script_async(script_path: Path, timeout: int = 300) -> tuple[bool, str]:
    """Run a Python script as a subprocess and return (success, output).

    Returns (True, stdout) on success, (False, error_message) on failure.
    """
    if not script_path.exists():
        return False, f"Script not found: {script_path}"

    try:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)  # Avoid nested session errors

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if process.returncode == 0:
            return True, stdout_text
        else:
            return False, f"Exit code {process.returncode}: {stderr_text or stdout_text}"

    except asyncio.TimeoutError:
        return False, f"Script timed out after {timeout}s"
    except Exception as e:
        return False, f"Exception running script: {e}"


# ------------------------------------------------------------------
# Gospel Bible Verses — Grace-focused devotional collection
# ------------------------------------------------------------------

GOSPEL_VERSES = [
    {
        "ref": "John 3:16",
        "text": "For God so loved the world that he gave his one and only Son, that whoever believes in him shall not perish but have eternal life.",
        "thought": "God's love wasn't conditional on your performance. He gave everything while you were still figuring it out. That's the kind of love you get to rest in today.",
    },
    {
        "ref": "John 10:28-29",
        "text": "I give them eternal life, and they shall never perish; no one will snatch them out of my hand. My Father, who has given them to me, is greater than all; no one can snatch them out of my Father's hand.",
        "thought": "You're held by two hands — the Son's and the Father's. Nothing in this world is strong enough to pry you loose. Go live today without fear.",
    },
    {
        "ref": "John 6:37",
        "text": "All those the Father gives me will come to me, and whoever comes to me I will never drive away.",
        "thought": "He will never drive you away. Not on your worst day, not after your biggest failure. The door is always open because He's the one holding it.",
    },
    {
        "ref": "John 8:36",
        "text": "So if the Son sets you free, you will be free indeed.",
        "thought": "Freedom isn't something you earn after enough good behavior. Jesus already set you free. Walk in it today — you have nothing to prove.",
    },
    {
        "ref": "John 1:16",
        "text": "Out of his fullness we have all received grace in place of grace already given.",
        "thought": "Grace on top of grace. It never runs out. Every morning you wake up to a fresh supply that has nothing to do with yesterday's scorecard.",
    },
    {
        "ref": "John 5:24",
        "text": "Very truly I tell you, whoever hears my word and believes him who sent me has eternal life and will not be judged but has crossed over from death to life.",
        "thought": "You have already crossed over. Past tense. The verdict is in and it's life. Enjoy today knowing the case is closed.",
    },
    {
        "ref": "John 6:40",
        "text": "For my Father's will is that everyone who looks to the Son and believes in him shall have eternal life, and I will raise them up at the last day.",
        "thought": "The Father's will is your eternal security. Not your willpower, not your discipline — His will. And His will cannot be broken.",
    },
    {
        "ref": "John 11:25-26",
        "text": "Jesus said to her, 'I am the resurrection and the life. The one who believes in me will live, even though they die; and whoever lives by believing in me will never die.'",
        "thought": "Death itself lost its grip the moment you believed. That's how complete the finished work of Christ is. Live boldly today.",
    },
    {
        "ref": "John 14:27",
        "text": "Peace I leave with you; my peace I give you. I do not give to you as the world gives. Do not let your hearts be troubled and do not be afraid.",
        "thought": "The world offers peace with conditions and fine print. Jesus offers peace as a gift — no strings attached. Receive it and breathe easy.",
    },
    {
        "ref": "John 15:9",
        "text": "As the Father has loved me, so have I loved you. Now remain in my love.",
        "thought": "The same love the Father has for the Son is the love Jesus has for you. Let that sink in. You're not on the outside looking in — you're family.",
    },
    {
        "ref": "John 15:16",
        "text": "You did not choose me, but I chose you and appointed you so that you might go and bear fruit — fruit that will last.",
        "thought": "He chose you before you chose Him. Your salvation started with His decision, not yours — and His decisions don't have a return policy.",
    },
    {
        "ref": "John 17:23",
        "text": "I in them and you in me — so that they may be brought to complete unity. Then the world will know that you sent me and have loved them even as you have loved me.",
        "thought": "God loves you the same way He loves Jesus. Not less, not a watered-down version. The same. Let that fuel your confidence today.",
    },
    {
        "ref": "John 19:30",
        "text": "When he had received the drink, Jesus said, 'It is finished.' With that, he bowed his head and gave up his spirit.",
        "thought": "Three words that changed everything. It is finished. Not 'it is started' or 'it is mostly done.' The price is fully paid. Stop trying to add to it.",
    },
    {
        "ref": "Luke 7:50",
        "text": "Jesus said to the woman, 'Your faith has saved you; go in peace.'",
        "thought": "Faith — not perfection, not performance, not penance. Faith. And then He says go in peace. That's your marching order today: go in peace.",
    },
    {
        "ref": "Luke 15:7",
        "text": "I tell you that in the same way there will be more rejoicing in heaven over one sinner who repents than over ninety-nine righteous persons who do not need to repent.",
        "thought": "Heaven threw a party when you came home. You're not a burden to God — you're a cause for celebration. Live like someone who's celebrated.",
    },
    {
        "ref": "Luke 12:32",
        "text": "Do not be afraid, little flock, for your Father has been pleased to give you the kingdom.",
        "thought": "It pleased Him to give it to you. Not grudgingly, not reluctantly — with pleasure. You're not begging for scraps; you've been given the whole kingdom.",
    },
    {
        "ref": "Luke 23:43",
        "text": "Jesus answered him, 'Truly I tell you, today you will be with me in paradise.'",
        "thought": "The thief on the cross had no time for good works, no baptism, no church attendance. Just faith. And Jesus said 'today — paradise.' Grace is that immediate.",
    },
    {
        "ref": "Luke 19:10",
        "text": "For the Son of Man came to seek and to save the lost.",
        "thought": "He came looking for you, not the other way around. You were found by a Savior who doesn't lose what He finds.",
    },
    {
        "ref": "Luke 6:38",
        "text": "Give, and it will be given to you. A good measure, pressed down, shaken together and running over, will be poured into your lap.",
        "thought": "God's generosity toward you overflows. He doesn't measure out blessings with an eyedropper — He pours until it runs over. Enjoy the abundance.",
    },
    {
        "ref": "Luke 1:37",
        "text": "For no word from God will ever fail.",
        "thought": "Every promise He made about your salvation, your security, your future — none of it will fail. Bank on it.",
    },
    {
        "ref": "Matthew 11:28-30",
        "text": "Come to me, all you who are weary and burdened, and I will give you rest. Take my yoke upon you and learn from me, for I am gentle and humble in heart, and you will find rest for your souls. For my yoke is easy and my burden is light.",
        "thought": "If your faith feels heavy, you're carrying something Jesus never asked you to pick up. His yoke is easy. Put the extras down and enjoy the rest He's offering.",
    },
    {
        "ref": "Matthew 7:11",
        "text": "If you, then, though you are evil, know how to give good gifts to your children, how much more will your Father in heaven give good things to those who ask him!",
        "thought": "God is a better Father than the best dad you can imagine. He loves giving good things to His kids. Ask freely and expect generously.",
    },
    {
        "ref": "Matthew 28:20",
        "text": "And surely I am with you always, to the very end of the age.",
        "thought": "Always means always. Not 'when you're behaving' or 'when you feel spiritual.' He is with you right now, today, in whatever you're facing.",
    },
    {
        "ref": "Matthew 10:29-31",
        "text": "Are not two sparrows sold for a penny? Yet not one of them will fall to the ground outside your Father's care. And even the very hairs of your head are all numbered. So don't be afraid; you are worth more than many sparrows.",
        "thought": "If God tracks every sparrow and numbers every hair on your head, He's certainly not going to lose track of your soul. You are valued beyond measure.",
    },
    {
        "ref": "Matthew 6:26",
        "text": "Look at the birds of the air; they do not sow or reap or store away in barns, and yet your heavenly Father feeds them. Are you not much more valuable than they?",
        "thought": "The birds don't stress about tomorrow and God feeds them anyway. You're worth infinitely more. Let go of the anxiety and trust the Provider.",
    },
    {
        "ref": "Mark 10:27",
        "text": "Jesus looked at them and said, 'With man this is impossible, but not with God; all things are possible with God.'",
        "thought": "Salvation was impossible for us to earn — that's the whole point. God did the impossible part. All you had to do was receive it.",
    },
    {
        "ref": "Mark 2:17",
        "text": "On hearing this, Jesus said to them, 'It is not the healthy who need a doctor, but the sick. I have not come to call the righteous, but sinners.'",
        "thought": "Jesus didn't come for people who had it all together. He came for the rest of us. Your mess is exactly what qualifies you for His grace.",
    },
    {
        "ref": "Mark 11:24",
        "text": "Therefore I tell you, whatever you ask for in prayer, believe that you have received it, and it will be yours.",
        "thought": "Prayer isn't about convincing a reluctant God. It's about receiving from a generous Father who already wants to give. Believe and receive.",
    },
    {
        "ref": "John 4:14",
        "text": "But whoever drinks the water I give them will never thirst. Indeed, the water I give them will become in them a spring of water welling up to eternal life.",
        "thought": "The satisfaction Jesus gives isn't temporary. It's a spring that never dries up. Stop chasing things that leave you thirsty and drink deep from the source.",
    },
    {
        "ref": "John 14:6",
        "text": "Jesus answered, 'I am the way and the truth and the life. No one comes to the Father except through me.'",
        "thought": "One way, but it's wide open to everyone who believes. Jesus didn't make salvation complicated — He made it personal. Through Him, you have full access to the Father.",
    },
    {
        "ref": "Matthew 19:26",
        "text": "Jesus looked at them and said, 'With man this is impossible, but with God all things are possible.'",
        "thought": "Whatever feels impossible today — the project, the challenge, the breakthrough you need — remember who's on your side. With God, the impossible is just Tuesday.",
    },
    {
        "ref": "John 16:33",
        "text": "I have told you these things, so that in me you may have peace. In this world you will have trouble. But take heart! I have overcome the world.",
        "thought": "He didn't promise a trouble-free life. He promised something better — that He's already overcome it all. Take heart today. The victory is already won.",
    },
    {
        "ref": "Luke 10:20",
        "text": "However, do not rejoice that the spirits submit to you, but rejoice that your names are written in heaven.",
        "thought": "Your name is written in heaven. Not in pencil — in permanent ink, by the hand of God Himself. That's the real win, and nobody can erase it.",
    },
    {
        "ref": "Matthew 9:13",
        "text": "But go and learn what this means: 'I desire mercy, not sacrifice.' For I have not come to call the righteous, but sinners.",
        "thought": "God isn't looking for your sacrifices or your striving. He desires mercy — and He's already shown it to you. Receive it and pass it along.",
    },
    {
        "ref": "John 6:47",
        "text": "Very truly I tell you, the one who believes has eternal life.",
        "thought": "Has. Present tense. Not 'might have' or 'will have if you're good enough.' You believe, you have it. It's that simple and that secure.",
    },
    {
        "ref": "Luke 15:20",
        "text": "So he got up and went to his father. But while he was still a long way off, his father saw him and was filled with compassion for him; he ran to his son, threw his arms around him and kissed him.",
        "thought": "The Father doesn't wait for you to clean up before He runs to you. He sees you coming from a long way off and He sprints. That's the heart of God toward you.",
    },
]


def _get_daily_verse(now_ct: datetime) -> dict:
    """Pick the Gospel verse for today based on day-of-year cycling."""
    day_of_year = now_ct.timetuple().tm_yday
    index = day_of_year % len(GOSPEL_VERSES)
    return GOSPEL_VERSES[index]


# ------------------------------------------------------------------
# Morning Report Task (5:00 AM CT)
# ------------------------------------------------------------------

def _get_latest_scan_report() -> Optional[str]:
    """Find the most recent daily scan report from cron-jobs/reports/."""
    if not CRON_REPORTS_DIR.exists():
        return None

    reports = sorted(CRON_REPORTS_DIR.glob("*_daily_scan.md"), reverse=True)
    if not reports:
        return None

    try:
        content = reports[0].read_text(errors="replace")
        return content if content.strip() else None
    except Exception:
        logger.exception("Failed to read scan report")
        return None


def _get_latest_todo() -> Optional[str]:
    """Find the most recent daily todo file from /opt/goliath/reports/."""
    if not REPORTS_DIR.exists():
        return None

    # Look for files matching common todo naming patterns
    todo_files = []
    for pattern in ["*todo*", "*to-do*", "*TODO*"]:
        todo_files.extend(REPORTS_DIR.glob(pattern))

    if not todo_files:
        return None

    # Sort by modification time, most recent first
    todo_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    try:
        content = todo_files[0].read_text(errors="replace")
        return content.strip() if content.strip() else None
    except Exception:
        logger.exception("Failed to read todo file")
        return None


def _build_project_health_summary() -> str:
    """Build a quick project health summary by scanning project directories."""
    projects_dir = REPO_ROOT / "projects"
    lines = []

    for slug, info in PROJECTS.items():
        project_path = projects_dir / slug
        if not project_path.exists():
            lines.append(f"  <code>{info['name']}</code> — <i>no folder</i>")
            continue

        # Check which data folders have content
        status_parts = []
        for folder in ["pod", "schedule", "constraints"]:
            folder_path = project_path / folder
            if folder_path.exists():
                files = [f for f in folder_path.rglob("*") if f.is_file() and f.name != ".gitkeep"]
                if files:
                    status_parts.append(f"{folder}: {len(files)}")

        if status_parts:
            lines.append(f"  <code>{info['name']}</code> — {', '.join(status_parts)}")
        else:
            lines.append(f"  <code>{info['name']}</code> — <i>awaiting data</i>")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Morning Report — structured data gathering (for PDF)
# ------------------------------------------------------------------

def _gather_open_action_items() -> list[dict]:
    """Query memory DB for all open (unresolved) action items.

    Returns list of dicts with keys: id, created_at, project_key, summary, detail.
    """
    if not MEMORY_DB_PATH.exists():
        return []
    try:
        conn = sqlite3.connect(str(MEMORY_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT id, created_at, project_key, summary, detail "
            "FROM memories WHERE category = 'action_item' AND resolved = 0 "
            "ORDER BY created_at DESC"
        )
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"Could not query memory DB for action items: {e}")
        return []


def _gather_project_health() -> list[dict]:
    """Build project health data as a list of dicts for PDF/Excel consumption.

    Returns list of dicts with keys: slug, name, pod, schedule, constraints,
    constraints_open, schedule_status, key_risk.
    """
    projects_dir = REPO_ROOT / "projects"
    results = []

    for slug, info in PROJECTS.items():
        project_path = projects_dir / slug
        name = info["name"]
        row = {
            "slug": slug,
            "name": name,
            "pod": 0,
            "schedule": 0,
            "constraints": 0,
            "constraints_open": 0,
            "schedule_status": "On Track",
            "key_risk": "None identified",
        }

        if project_path.exists():
            for folder in ["pod", "schedule", "constraints"]:
                folder_path = project_path / folder
                if folder_path.exists():
                    files = [
                        f for f in folder_path.rglob("*")
                        if f.is_file() and f.name != ".gitkeep"
                    ]
                    row[folder] = len(files)

            # Infer schedule status from constraint count
            open_count = row["constraints_open"] or row["constraints"]
            if open_count > 10:
                row["schedule_status"] = "At Risk"
                row["key_risk"] = f"{open_count} open constraints"
            elif open_count > 5:
                row["schedule_status"] = "Monitor"
                row["key_risk"] = f"{open_count} open constraints"

        results.append(row)

    return results


def _gather_constraint_movement() -> dict:
    """Get constraint movement data.

    NOTE: Constraint snapshot functionality has been removed. Constraint data
    is now accessed live via the Convex API / MCP server. This function returns
    an empty structure for backward compatibility with report formatting.

    Returns dict with keys: new, resolved, status_changed, priority_changed,
    total_current, per_project (aggregated counts by project).
    """
    return {
        "new": [],
        "resolved": [],
        "status_changed": [],
        "priority_changed": [],
        "total_current": 0,
        "per_project": {},
    }


def _gather_followup_items() -> dict:
    """Query the follow-up DB for overdue and due-today items.

    Returns dict with keys: overdue (list of dicts), due_today (list of dicts).
    """
    result = {"overdue": [], "due_today": []}

    if not FOLLOWUP_DB_PATH.exists():
        return result

    try:
        conn = sqlite3.connect(str(FOLLOWUP_DB_PATH))
        conn.row_factory = sqlite3.Row
        today = datetime.now(CT).strftime("%Y-%m-%d")

        cursor = conn.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date < ? AND status = 'pending' "
            "ORDER BY follow_up_date ASC",
            (today,),
        )
        result["overdue"] = [dict(row) for row in cursor.fetchall()]

        cursor = conn.execute(
            "SELECT * FROM follow_ups WHERE follow_up_date = ? AND status = 'pending' "
            "ORDER BY created_at ASC",
            (today,),
        )
        result["due_today"] = [dict(row) for row in cursor.fetchall()]

        conn.close()
    except Exception as e:
        logger.warning(f"Could not query follow-up DB: {e}")

    return result


# ------------------------------------------------------------------
# Morning Report — PDF generation (ReportLab)
# ------------------------------------------------------------------

def _generate_morning_pdf(
    output_path: Path,
    date_str: str,
    verse: dict,
    action_items: list[dict],
    project_health: list[dict],
    constraint_movement: dict,
    followup_items: dict,
    scan_content: Optional[str],
) -> bool:
    """Generate a professional morning report PDF using ReportLab.

    Returns True on success, False on failure.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            BaseDocTemplate, Frame, HRFlowable,
            NextPageTemplate, PageBreak, PageTemplate,
            Paragraph, Spacer, Table, TableStyle,
        )
    except ImportError:
        logger.error("reportlab not installed — cannot generate morning report PDF")
        return False

    # --- Brand colors (DSC blue #003366 as primary accent) ---
    DSC_BLUE = colors.HexColor("#003366")
    NAVY = colors.HexColor("#003366")
    ACCENT_BLUE = colors.HexColor("#336699")
    LIGHT_BLUE = colors.HexColor("#E8F0FE")
    RED = colors.HexColor("#CC0000")
    AMBER = colors.HexColor("#CC8800")
    GREEN = colors.HexColor("#228B22")
    WHITE = colors.white
    BLACK = colors.black
    LIGHT_GREY = colors.HexColor("#F5F5F5")
    MID_GREY = colors.HexColor("#E0E0E0")
    DARK_GREY = colors.HexColor("#666666")
    TABLE_HEADER_BG = DSC_BLUE
    TABLE_ALT_ROW = colors.HexColor("#F2F6FA")

    _base_styles = getSampleStyleSheet()

    def _s(name, **kw):
        return ParagraphStyle(name, parent=_base_styles["Normal"], **kw)

    # Styles
    S_H1 = _s("MRH1", fontName="Helvetica-Bold", fontSize=14,
               textColor=NAVY, leading=20, spaceBefore=14, spaceAfter=6)
    S_H2 = _s("MRH2", fontName="Helvetica-Bold", fontSize=11,
               textColor=NAVY, leading=16, spaceBefore=10, spaceAfter=4)
    S_BODY = _s("MRBody", fontName="Helvetica", fontSize=9,
                textColor=BLACK, leading=12, spaceAfter=3)
    S_BODY_BOLD = _s("MRBodyBold", fontName="Helvetica-Bold", fontSize=9,
                     textColor=BLACK, leading=12, spaceAfter=3)
    S_SMALL = _s("MRSmall", fontName="Helvetica", fontSize=7.5,
                 textColor=DARK_GREY, leading=10)
    S_VERSE = _s("MRVerse", fontName="Helvetica-Oblique", fontSize=9.5,
                 textColor=colors.HexColor("#333333"), leading=13,
                 leftIndent=12, rightIndent=12, spaceBefore=4, spaceAfter=4,
                 backColor=colors.HexColor("#FFF8E8"),
                 borderWidth=0.5, borderColor=colors.HexColor("#E8D8A0"),
                 borderPadding=8)
    S_TH = _s("MRTH", fontName="Helvetica-Bold", fontSize=8,
              textColor=WHITE, leading=10)
    S_TC = _s("MRTC", fontName="Helvetica", fontSize=8,
              textColor=BLACK, leading=10)
    S_ACTION = _s("MRAction", fontName="Helvetica", fontSize=8.5,
                  textColor=BLACK, leading=11, leftIndent=8, spaceAfter=2)

    w, h = letter
    now = datetime.now(CT)
    formatted_date = now.strftime("%B %d, %Y")

    # --- Page templates ---
    def _first_page(canvas, doc):
        canvas.saveState()
        # Tall header band
        header_h = 90
        canvas.setFillColor(DSC_BLUE)
        canvas.rect(0, h - header_h, w, header_h, stroke=0, fill=1)
        # Accent line under header
        canvas.setFillColor(ACCENT_BLUE)
        canvas.rect(0, h - header_h, w, 3, stroke=0, fill=1)
        # Title
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(0.75 * inch, h - 40, "GOLIATH Morning Report")
        canvas.setFont("Helvetica", 11)
        canvas.setFillColor(colors.HexColor("#B0C4DE"))
        canvas.drawString(0.75 * inch, h - 58, formatted_date)
        # Summary stats
        n_items = len(action_items)
        n_projects = len(project_health)
        n_changes = (len(constraint_movement.get("new", []))
                     + len(constraint_movement.get("resolved", [])))
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#8FAFD0"))
        canvas.drawString(
            0.75 * inch, h - 74,
            f"{n_items} action items  |  {n_projects} projects  |  "
            f"{n_changes} constraint changes (24h)",
        )
        # Footer
        canvas.setStrokeColor(MID_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
        canvas.setFillColor(DARK_GREY)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(0.75 * inch, 0.35 * inch,
                          "Generated by GOLIATH Scheduler")
        canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    def _later_pages(canvas, doc):
        canvas.saveState()
        # Thin header bar
        canvas.setFillColor(DSC_BLUE)
        canvas.rect(0, h - 45, w, 45, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(0.75 * inch, h - 30, "GOLIATH Morning Report")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#B0C4DE"))
        canvas.drawRightString(w - 0.75 * inch, h - 30, formatted_date)
        # Footer
        canvas.setStrokeColor(MID_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
        canvas.setFillColor(DARK_GREY)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(0.75 * inch, 0.35 * inch,
                          "Generated by GOLIATH Scheduler")
        canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    first_frame = Frame(0.75 * inch, 0.65 * inch,
                        w - 1.5 * inch, h - 1.65 * inch, id="first")
    later_frame = Frame(0.75 * inch, 0.65 * inch,
                        w - 1.5 * inch, h - 1.25 * inch, id="later")

    first_tmpl = PageTemplate(id="First", frames=[first_frame], onPage=_first_page)
    later_tmpl = PageTemplate(id="Later", frames=[later_frame], onPage=_later_pages)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(output_path),
        pagesize=letter,
        title=f"GOLIATH Morning Report — {formatted_date}",
        author="GOLIATH System",
    )
    doc.addPageTemplates([first_tmpl, later_tmpl])

    elements = []

    def _esc(text):
        """Escape HTML entities for ReportLab Paragraph."""
        if not text:
            return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ---- Section 1: Gospel Verse ----
    elements.append(Paragraph("TODAY'S GOSPEL WORD", S_H1))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                               spaceBefore=2, spaceAfter=6))
    verse_text = _esc(verse.get("text", ""))
    verse_ref = _esc(verse.get("ref", ""))
    verse_thought = _esc(verse.get("thought", ""))
    elements.append(Paragraph(f'<i>"{verse_text}"</i><br/>— {verse_ref}', S_VERSE))
    if verse_thought:
        elements.append(Paragraph(f'<i>{verse_thought}</i>', S_SMALL))
    elements.append(Spacer(1, 8))

    # ---- Section 2: Open Action Items ----
    elements.append(Paragraph(
        f'OPEN ACTION ITEMS ({len(action_items)} pending)', S_H1))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                               spaceBefore=2, spaceAfter=6))

    if action_items:
        # Group by project
        by_project = {}
        for item in action_items:
            key = item.get("project_key") or "general"
            by_project.setdefault(key, []).append(item)

        # General items first
        general = by_project.pop("general", [])
        if general:
            elements.append(Paragraph("General / System", S_H2))
            for item in general:
                date = item["created_at"][:10]
                summary = _esc(item.get("summary", ""))
                elements.append(Paragraph(
                    f'<b>[{date}]</b> {summary}', S_ACTION))

        # Project items
        for proj_key in sorted(by_project.keys()):
            proj_name = PROJECTS.get(proj_key, {}).get("name", proj_key)
            elements.append(Paragraph(_esc(proj_name), S_H2))
            for item in by_project[proj_key]:
                date = item["created_at"][:10]
                summary = _esc(item.get("summary", ""))
                elements.append(Paragraph(
                    f'<b>[{date}]</b> {summary}', S_ACTION))
    else:
        elements.append(Paragraph(
            '<i>No open action items. Everything is resolved.</i>', S_BODY))

    elements.append(Spacer(1, 8))

    # ---- Section 3: Project Health Summary ----
    elements.append(Paragraph(
        f'PORTFOLIO HEALTH SUMMARY ({len(project_health)} projects)', S_H1))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                               spaceBefore=2, spaceAfter=6))

    health_header = [
        Paragraph("<b>Project</b>", S_TH),
        Paragraph("<b>POD</b>", S_TH),
        Paragraph("<b>Schedule</b>", S_TH),
        Paragraph("<b>Constraints</b>", S_TH),
        Paragraph("<b>Open</b>", S_TH),
        Paragraph("<b>Status</b>", S_TH),
        Paragraph("<b>Key Risk</b>", S_TH),
    ]
    health_data = [health_header]

    for p in project_health:
        status = p.get("schedule_status", "On Track")
        if status == "At Risk":
            status_color = "#CC0000"
        elif status == "Monitor":
            status_color = "#CC8800"
        else:
            status_color = "#228B22"

        health_data.append([
            Paragraph(_esc(p["name"]), S_TC),
            Paragraph(str(p.get("pod", 0)), S_TC),
            Paragraph(str(p.get("schedule", 0)), S_TC),
            Paragraph(str(p.get("constraints", 0)), S_TC),
            Paragraph(str(p.get("constraints_open", 0)), S_TC),
            Paragraph(f'<font color="{status_color}"><b>{_esc(status)}</b></font>', S_TC),
            Paragraph(_esc(p.get("key_risk", "None")[:60]), S_TC),
        ])

    col_widths = [1.3 * inch, 0.5 * inch, 0.65 * inch, 0.75 * inch,
                  0.5 * inch, 0.7 * inch, 2.1 * inch]
    health_table = Table(health_data, colWidths=col_widths, repeatRows=1)
    health_style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("GRID", (0, 0), (-1, -1), 0.5, MID_GREY),
        ("ALIGN", (1, 0), (4, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, TABLE_ALT_ROW]),
    ])
    health_table.setStyle(health_style)
    elements.append(health_table)
    elements.append(Spacer(1, 10))

    elements.append(NextPageTemplate("Later"))

    # ---- Section 4: Constraint Movement (24h) ----
    elements.append(Paragraph("CONSTRAINT MOVEMENT (24h)", S_H1))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                               spaceBefore=2, spaceAfter=6))

    total_c = constraint_movement.get("total_current", 0)
    elements.append(Paragraph(
        f'<i>{total_c} total constraints tracked</i>', S_SMALL))

    new_items = constraint_movement.get("new", [])
    resolved_items = constraint_movement.get("resolved", [])
    status_items = constraint_movement.get("status_changed", [])
    priority_items = constraint_movement.get("priority_changed", [])
    total_changes = len(new_items) + len(resolved_items) + len(status_items) + len(priority_items)

    if total_changes == 0:
        elements.append(Paragraph(
            '<i>No constraint changes in the last 24 hours.</i>', S_BODY))
    else:
        elements.append(Paragraph(
            f'<b>{total_changes} change(s) detected:</b>', S_BODY_BOLD))

        if new_items:
            elements.append(Paragraph(
                f'<font color="#228B22"><b>New Constraints ({len(new_items)})</b></font>', S_H2))
            for item in new_items[:10]:
                elements.append(Paragraph(
                    f'<b>[{_esc(item.get("priority", "?"))}]</b> '
                    f'{_esc(item.get("project", "?"))}: {_esc(item.get("description", "?"))}',
                    S_ACTION))

        if resolved_items:
            elements.append(Paragraph(
                f'<font color="#336699"><b>Resolved ({len(resolved_items)})</b></font>', S_H2))
            for item in resolved_items[:10]:
                elements.append(Paragraph(
                    f'{_esc(item.get("project", "?"))}: {_esc(item.get("description", "?"))}',
                    S_ACTION))

        if status_items:
            elements.append(Paragraph(
                f'<b>Status Changed ({len(status_items)})</b>', S_H2))
            for item in status_items[:10]:
                elements.append(Paragraph(
                    f'{_esc(item.get("project", "?"))}: '
                    f'{_esc(item.get("old_status", "?"))} -> {_esc(item.get("new_status", "?"))} '
                    f'-- {_esc(item.get("description", "?"))}',
                    S_ACTION))

        if priority_items:
            elements.append(Paragraph(
                f'<b>Priority Changed ({len(priority_items)})</b>', S_H2))
            for item in priority_items[:10]:
                elements.append(Paragraph(
                    f'{_esc(item.get("project", "?"))}: '
                    f'{_esc(item.get("old_priority", "?"))} -> {_esc(item.get("new_priority", "?"))} '
                    f'({_esc(item.get("direction", "changed"))}) -- '
                    f'{_esc(item.get("description", "?"))}',
                    S_ACTION))

    elements.append(Spacer(1, 10))

    # ---- Section 5: Follow-Up Queue ----
    elements.append(Paragraph("FOLLOW-UP QUEUE", S_H1))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                               spaceBefore=2, spaceAfter=6))

    overdue = followup_items.get("overdue", [])
    due_today = followup_items.get("due_today", [])

    if not overdue and not due_today:
        elements.append(Paragraph(
            '<i>No follow-ups due or overdue today.</i>', S_BODY))
    else:
        if overdue:
            elements.append(Paragraph(
                f'<font color="#CC0000"><b>Overdue ({len(overdue)})</b></font>', S_H2))
            for item in overdue[:12]:
                project = _esc(str(item.get("project_key", "?")))
                commitment = _esc(str(item.get("commitment", ""))[:120])
                due = item.get("follow_up_date", "?")
                owner = _esc(str(item.get("owner", "?")))
                elements.append(Paragraph(
                    f'<b>[{project}]</b> {commitment}<br/>'
                    f'<i>Due: {due} | Owner: {owner}</i>',
                    S_ACTION))

        if due_today:
            elements.append(Paragraph(
                f'<font color="#CC8800"><b>Due Today ({len(due_today)})</b></font>', S_H2))
            for item in due_today[:12]:
                project = _esc(str(item.get("project_key", "?")))
                commitment = _esc(str(item.get("commitment", ""))[:120])
                owner = _esc(str(item.get("owner", "?")))
                elements.append(Paragraph(
                    f'<b>[{project}]</b> {commitment}<br/>'
                    f'<i>Owner: {owner}</i>',
                    S_ACTION))

    elements.append(Spacer(1, 10))

    # ---- Section 6: Scan Findings (condensed) ----
    if scan_content:
        elements.append(Paragraph("LATEST DAILY SCAN", S_H1))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                                   spaceBefore=2, spaceAfter=6))
        # Convert markdown to plain-ish text for PDF (strip markdown syntax)
        cleaned = scan_content
        # Strip markdown headings
        cleaned = re.sub(r"^#{1,4}\s+", "", cleaned, flags=re.MULTILINE)
        # Strip bold/italic markers
        cleaned = cleaned.replace("**", "").replace("__", "")
        # Limit length for PDF
        if len(cleaned) > 4000:
            cleaned = cleaned[:3800] + "\n\n... (truncated — see full .md file)"
        for line in cleaned.split("\n"):
            line = line.strip()
            if not line:
                elements.append(Spacer(1, 3))
            elif line.startswith("- ") or line.startswith("* "):
                elements.append(Paragraph(
                    f'  {_esc(line)}', S_ACTION))
            else:
                elements.append(Paragraph(_esc(line), S_BODY))

    # Build the PDF
    try:
        doc.build(elements)
        logger.info(f"Morning report PDF generated: {output_path}")
        return True
    except Exception:
        logger.exception(f"Failed to build morning report PDF: {output_path}")
        return False




def _markdown_to_html(text: str) -> str:
    """Convert basic Markdown formatting to Telegram HTML.

    Handles:
      - # headings -> <b>headings</b>
      - **bold** -> <b>bold</b>
      - *italic* -> <i>italic</i>
      - `code` -> <code>code</code>
      - ```blocks``` -> <pre>blocks</pre>
      - Strips emoji shortcodes that use colons

    This is intentionally simple — not a full Markdown parser.
    """
    import re

    # Code blocks first (``` ... ```)
    text = re.sub(r"```(?:\w+)?\n(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)

    # Inline code
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)

    # Headings: # Title -> <b>Title</b>
    text = re.sub(r"^#{1,4}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Bold: **text** -> <b>text</b>
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Italic: *text* -> <i>text</i> (but not inside HTML tags)
    text = re.sub(r"(?<![</>])\*(?!\*)(.+?)(?<!\*)\*(?![*</>])", r"<i>\1</i>", text)

    # Strip bare markdown link syntax [text](url) -> text (url)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'\1 (\2)', text)

    return text


async def task_morning_report(scheduler: "Scheduler") -> None:
    """5:00 AM CT — Generate morning report PDF and send as attachment.

    Instead of sending the full report as inline text, this:
      1. Gathers all data (action items, project health, constraints, follow-ups, scan)
      2. Generates a PDF report
      3. Sends a SHORT notification message to Telegram
      4. Sends the PDF as a Telegram document attachment
    """
    if not scheduler.bot:
        logger.error("Morning report: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Morning report: no chat ID configured (set REPORT_CHAT_ID)")
        return

    now_ct = datetime.now(CT)
    date_iso = now_ct.strftime("%Y-%m-%d")
    date_display = now_ct.strftime("%A, %B %d, %Y")
    is_sunday = now_ct.weekday() == 6  # 6 = Sunday

    logger.info("Morning report: gathering data...")

    # ---- Step 1: Gather ALL structured data ----
    verse = _get_daily_verse(now_ct)
    action_items = _gather_open_action_items()
    project_health = _gather_project_health()
    constraint_movement = _gather_constraint_movement()
    followup_items = _gather_followup_items()
    scan_content = _get_latest_scan_report()

    # ---- Step 2: Build short notification summary ----
    n_items = len(action_items)
    n_changes = (len(constraint_movement.get("new", []))
                 + len(constraint_movement.get("resolved", [])))
    n_overdue = len(followup_items.get("overdue", []))
    n_due = len(followup_items.get("due_today", []))

    summary_parts = []
    if n_items > 0:
        summary_parts.append(f"{n_items} open action items")
    if n_changes > 0:
        summary_parts.append(f"{n_changes} constraint changes overnight")
    if n_overdue > 0:
        summary_parts.append(f"{n_overdue} overdue follow-ups")
    elif n_due > 0:
        summary_parts.append(f"{n_due} follow-ups due today")
    if not summary_parts:
        summary_parts.append("all clear across the portfolio")

    summary_line = "; ".join(summary_parts) + "."

    if is_sunday:
        notification = (
            f"\U0001f4cb Monday Prep Report ready \u2014 {date_display}.\n"
            f"{summary_line}\n"
            f"PDF attached below."
        )
        report_label = "Monday Prep Report"
        file_slug = "monday-prep-report"
    else:
        notification = (
            f"\u2600\ufe0f Morning report ready \u2014 {date_display}.\n"
            f"{summary_line}\n"
            f"PDF attached below."
        )
        report_label = "Morning Report"
        file_slug = "morning-report"

    # ---- Step 3: Generate PDF report ----
    report_dir = REPORTS_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = report_dir / f"{date_iso}-{file_slug}.pdf"

    # Generate PDF
    logger.info("Morning report: generating PDF...")
    try:
        pdf_ok = _generate_morning_pdf(
            pdf_path, date_iso, verse, action_items, project_health,
            constraint_movement, followup_items, scan_content,
        )
    except Exception:
        logger.exception("Morning report: PDF generation raised an exception")
        pdf_ok = False

    # ---- Step 4: Send short notification ----
    await _send_telegram(scheduler.bot, chat_id, notification)
    logger.info(f"Morning report notification sent to chat_id={chat_id}")

    # ---- Step 5: Send PDF attachment ----
    pdf_sent = False
    if pdf_ok and pdf_path.exists():
        pdf_sent = await _send_telegram_document(
            scheduler.bot, chat_id, pdf_path,
            caption=f"<b>{report_label} (PDF)</b> \u2014 {date_iso}",
        )

    # ---- Summary log ----
    logger.info(
        f"Morning report complete: PDF {'sent' if pdf_sent else 'FAILED'} "
        f"({pdf_path.name})"
    )

    if not pdf_sent:
        await _send_telegram(
            scheduler.bot, chat_id,
            f"<i>Note: Morning report PDF generation or delivery failed.</i>"
        )


# ------------------------------------------------------------------
# Daily Scan Task (11:00 PM CT)
# ------------------------------------------------------------------

async def task_daily_scan(scheduler: "Scheduler") -> None:
    """11:00 PM CT — Run the daily scan (calls the existing cron-job script)."""
    if not scheduler.bot:
        logger.error("Daily scan: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Daily scan: no chat ID configured (set REPORT_CHAT_ID)")
        return

    logger.info("Daily scan: starting project scan...")

    # Run the existing daily_scan.py as a subprocess
    scan_script = CRON_JOBS_DIR / "daily_scan.py"
    if not scan_script.exists():
        await _send_telegram(
            scheduler.bot, chat_id,
            "<b>Daily Scan Error</b>\n<code>daily_scan.py not found</code>"
        )
        return

    try:
        env = dict(__import__("os").environ)
        env.pop("CLAUDECODE", None)

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(scan_script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=1200  # 20 minutes
        )

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if process.returncode == 0:
            logger.info(f"Daily scan completed successfully")
            # Send a notification that the scan is done
            await _send_telegram(
                scheduler.bot, chat_id,
                f"<b>Daily Scan Complete</b>\n"
                f"<i>{datetime.now(CT).strftime('%I:%M %p CT')}</i>\n\n"
                f"Report saved. It will be included in tomorrow's morning report.\n\n"
                f"<code>{stdout_text[-500:] if stdout_text else 'No output'}</code>"
            )
        else:
            logger.error(f"Daily scan failed (rc={process.returncode})")
            error_info = stderr_text[:1000] or stdout_text[:1000] or "No error output"
            await _send_telegram(
                scheduler.bot, chat_id,
                f"<b>Daily Scan Failed</b>\n"
                f"<i>Exit code: {process.returncode}</i>\n\n"
                f"<pre>{error_info}</pre>"
            )

    except asyncio.TimeoutError:
        logger.error("Daily scan timed out after 12 minutes")
        await _send_telegram(
            scheduler.bot, chat_id,
            "<b>Daily Scan Timed Out</b>\n"
            "<i>The scan did not complete within 12 minutes.</i>"
        )
    except Exception as e:
        logger.exception("Daily scan error")
        await _send_telegram(
            scheduler.bot, chat_id,
            f"<b>Daily Scan Error</b>\n<pre>{str(e)[:500]}</pre>"
        )


# ------------------------------------------------------------------
# Daily Constraints Folder Task (midnight CT)
# ------------------------------------------------------------------

async def task_daily_constraints_folder(scheduler: "Scheduler") -> None:
    """12:05 AM CT — Create the daily constraints folder."""
    script = CRON_JOBS_DIR / "daily_constraints_folder.py"
    if not script.exists():
        logger.error("daily_constraints_folder.py not found")
        return

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=60
        )

        if process.returncode == 0:
            logger.info("Daily constraints folder created")
        else:
            logger.error(
                f"daily_constraints_folder failed (rc={process.returncode}): "
                f"{stderr.decode(errors='replace')[:200]}"
            )
    except Exception:
        logger.exception("Daily constraints folder task error")


async def task_folder_cleanup(scheduler: "Scheduler") -> None:
    """7:00 PM CT — Run folder organization scan and send results to Telegram."""
    script = CRON_JOBS_DIR / "folder_cleanup.py"
    if not script.exists():
        logger.error("folder_cleanup.py not found")
        return

    try:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)  # Avoid nested session errors

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=900  # 15 min max — Claude scan can take a while
        )

        if process.returncode == 0:
            logger.info("Folder cleanup scan completed and report sent")
        else:
            logger.error(
                f"folder_cleanup failed (rc={process.returncode}): "
                f"{stderr.decode(errors='replace')[:200]}"
            )
    except asyncio.TimeoutError:
        logger.error("Folder cleanup scan timed out after 900s")
    except Exception:
        logger.exception("Folder cleanup task error")


# ------------------------------------------------------------------
# Proactive Thinking Sessions
# Migrated from job_queue to this custom scheduler for reliable
# single-fire execution. Protected by three layers:
#   1. Scheduler in-flight set  (prevents concurrent execution)
#   2. Eager last_run stamp     (prevents re-trigger within 2 min)
#   3. Date-based dedup dict    (prevents same-day re-run)
# ------------------------------------------------------------------

async def task_proactive_morning(scheduler: "Scheduler") -> None:
    """6:00 AM CT — Morning proactive thinking session."""
    await _run_proactive_task(scheduler, session_type="morning")


async def task_proactive_evening(scheduler: "Scheduler") -> None:
    """6:00 PM CT — Evening proactive thinking session."""
    await _run_proactive_task(scheduler, session_type="evening")


async def _run_proactive_task(scheduler: "Scheduler", session_type: str) -> None:
    """Run a proactive thinking session through the custom scheduler.

    Layer 3 dedup guard (secondary safety net): keeps a module-level dict of
    {session_type: date_str}. If this session type already ran today, skip it.
    This survives even if the in-flight set or last_run were somehow bypassed.
    """
    if not scheduler.bot:
        logger.error(f"Proactive {session_type}: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error(f"Proactive {session_type}: no chat ID configured")
        return

    # --- Layer 3 dedup guard: check if we already ran this session today ---
    # This is a secondary safety net on top of the scheduler's in-flight set
    # (layer 1) and eager last_run stamp (layer 2). It ensures that even if
    # the scheduler somehow fires this task twice in one day (e.g. bot restart
    # near the scheduled time), the proactive session only runs once.
    now_ct = datetime.now(CT)
    today_str = now_ct.strftime("%Y-%m-%d")
    dedup_key = f"proactive_{session_type}"

    # Module-level dict survives across calls within the same process lifetime
    if not hasattr(_run_proactive_task, "_dedup"):
        _run_proactive_task._dedup = {}

    last_run_date = _run_proactive_task._dedup.get(dedup_key)
    if last_run_date == today_str:
        logger.warning(
            f"Proactive {session_type} already ran today ({today_str}) — "
            f"layer 3 dedup guard triggered, skipping duplicate"
        )
        return

    # Mark as running for today BEFORE executing
    _run_proactive_task._dedup[dedup_key] = today_str

    logger.info(f"Starting {session_type} proactive thinking session for chat_id={chat_id}")
    start_time = time.monotonic()

    try:
        # Import here to avoid circular imports
        from bot.agents.definitions import NIMROD
        from bot.agents.runner import get_runner
        from bot.memory.store import MemoryStore
        from bot.config import MEMORY_DB_PATH
        from bot.services.proactive import MORNING_PROMPT, EVENING_PROMPT

        # Build memory context
        memory = MemoryStore(MEMORY_DB_PATH)
        await memory.initialize()

        memory_parts = []
        recent = await memory.format_for_prompt(limit=15)
        if recent and recent != "(No relevant memories found.)":
            memory_parts.append(f"RECENT MEMORIES:\n{recent}")

        actions = await memory.get_action_items(resolved=False)
        if actions:
            action_lines = [f"- [{a.created_at[:10]}] {a.summary}" for a in actions[:10]]
            memory_parts.append(f"OPEN ACTION ITEMS:\n" + "\n".join(action_lines))
        elif hasattr(actions, "success") and not actions.success:
            memory_parts.append("OPEN ACTION ITEMS: unavailable (memory error)")

        memory_context = "\n\n".join(memory_parts) if memory_parts else "(No memories yet.)"

        # Choose prompt
        session_prompt = MORNING_PROMPT if session_type == "morning" else EVENING_PROMPT
        full_prompt = (
            f"PERSISTENT MEMORY:\n{memory_context}\n\n"
            f"---\n\n"
            f"{session_prompt}"
        )

        # Run Nimrod
        runner = get_runner()
        result = await runner.run(
            agent=NIMROD,
            task_prompt=full_prompt,
            no_tools=False,
        )

        duration = time.monotonic() - start_time

        if result.success and result.output:
            import re
            text = re.sub(r"```MEMORY_SAVE\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```SUBAGENT_REQUEST\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = re.sub(r"```FILE_CREATED\s*\n.*?```", "", result.output, flags=re.DOTALL)
            text = text.strip()

            if text:
                await _send_telegram(scheduler.bot, chat_id, text)
                logger.info(f"Proactive {session_type} session sent ({duration:.1f}s)")

                # Save to memory
                try:
                    await memory.save(
                        category="observation",
                        summary=f"Sent {session_type} proactive thinking message to user",
                        detail=text[:500],
                        source="nimrod",
                        tags=f"proactive,{session_type}",
                    )
                except Exception:
                    pass
            else:
                logger.warning(f"Proactive {session_type} session produced empty output")
        else:
            logger.error(f"Proactive {session_type} session failed: {result.error}")
            # Clear dedup so it can retry if manually triggered
            _run_proactive_task._dedup.pop(dedup_key, None)

    except Exception:
        logger.exception(f"Proactive {session_type} session error")
        # Clear dedup on error so it can retry
        _run_proactive_task._dedup.pop(dedup_key, None)


# ------------------------------------------------------------------
# Email polling task
# ------------------------------------------------------------------

async def task_email_poll(scheduler: "Scheduler") -> None:
    """Poll Gmail IMAP for inbound [INBOX:] emails and feed into message queue.

    This is an interval task (every 45s). It's lightweight — just an
    IMAP search + fetch cycle — so it doesn't need the full 15-minute timeout.

    Also handles automatic attachment filing for:
      - POD emails → projects/{key}/pod/
      - Constraints updates → dsc-constraints-production-reports/{date}/
    """
    # Import here to avoid circular imports at module load time
    from bot.services.email_poller import EmailPoller
    from bot.config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GMAIL_IMAP_HOST

    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        # Silently skip if not configured — don't spam logs every 45 seconds
        return

    # We need the message queue from bot_data. The scheduler holds a bot ref,
    # and bot._bot_data_ref is set during initialization (see main.py).
    bot = scheduler.bot
    queue = None
    if bot and hasattr(bot, "_bot_data_ref"):
        queue = bot._bot_data_ref.get("message_queue")

    if not queue:
        logger.debug("Email poll skipped — message queue not available yet")
        return

    poller = EmailPoller(
        address=GMAIL_ADDRESS,
        app_password=GMAIL_APP_PASSWORD,
        imap_host=GMAIL_IMAP_HOST,
    )
    poller.set_queue(queue)
    # Wire up bot so poller can send Telegram notifications for auto-filed attachments
    poller.set_bot(bot)

    try:
        count = await asyncio.wait_for(poller.poll(), timeout=60)
        if count > 0:
            logger.info(f"Email poll: {count} new inbound email(s) processed")
    except asyncio.TimeoutError:
        logger.warning("Email poll timed out after 60s")
    except Exception:
        logger.exception("Email poll error")


# ------------------------------------------------------------------
# Proactive Follow-Up — Daily PDF Report (replaces old Escalation Engine)
# ------------------------------------------------------------------

# DISABLED 2026-03-02 — redundant with morning report (user request)
# async def task_proactive_followup_report(scheduler: "Scheduler") -> None:
#     """Generate the daily Proactive Follow-Up PDF report.
#
#     Fires once daily at 5 PM CT Mon-Fri (end of day) and 4:15 PM Sunday.
#     Pulls ALL open constraints from ConstraintsPro, routes each to a specialist
#     "brain" for solution-oriented draft generation, generates ONE consolidated
#     PDF, and sends it to Telegram.
#
#     The PDF also includes any pending commitment-based follow-ups from the
#     followup queue database (action items from meetings), so the user has
#     ONE report covering everything they need to chase the next morning.
#
#     This replaces the old escalation engine that sent 50+ individual messages
#     AND the old follow-up queue scans that sent 30+ individual messages.
#     Now: one clean PDF with copy-paste-ready drafts organized by project and priority.
#     """
#     if not scheduler.bot:
#         logger.error("Proactive follow-up: no bot instance")
#         return
#
#     chat_id = _get_chat_id()
#     if not chat_id:
#         logger.error("Proactive follow-up: no chat ID configured (set REPORT_CHAT_ID)")
#         return
#
#     try:
#         from bot.services.proactive_followup import ProactiveFollowUpEngine
#         from bot.config import PROACTIVE_FOLLOWUP_DB_PATH
#
#         engine = ProactiveFollowUpEngine(PROACTIVE_FOLLOWUP_DB_PATH)
#         await engine.initialize()
#
#         constraints_count = await engine.run_daily_report(scheduler.bot, chat_id)
#
#         await engine.close()
#
#         if constraints_count > 0:
#             logger.info(
#                 f"Proactive follow-up: report generated with {constraints_count} constraint(s)"
#             )
#         else:
#             logger.info("Proactive follow-up: no constraints to report")
#
#     except Exception:
#         logger.exception("Proactive follow-up report task failed")


# ------------------------------------------------------------------
# Follow-Up Queue task (DISABLED 2026-03-01)
# Was: 10 AM and 4 PM CT, sending individual Telegram messages per item.
# Now: All follow-ups consolidated into the end-of-day PDF report via
#      task_proactive_followup_report. This function is kept but no longer
#      registered in create_scheduler().
# ------------------------------------------------------------------

async def task_follow_up_scan(scheduler: "Scheduler") -> None:
    """Run follow-up queue scan — checks for due commitments and approaching
    constraint deadlines.

    DEPRECATED (2026-03-01): No longer registered in the scheduler.
    Was firing 2x/day at 10 AM and 4 PM CT and sending individual Telegram
    messages per item (30+ messages = spam). Follow-ups are now consolidated
    into the end-of-day PDF report via task_proactive_followup_report.
    """
    if not scheduler.bot:
        logger.error("Follow-up scan: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Follow-up scan: no chat ID configured (set REPORT_CHAT_ID)")
        return

    try:
        from bot.services.followup import FollowUpQueue

        queue = FollowUpQueue(FOLLOWUP_DB_PATH)
        await queue.initialize()

        drafts_sent = await queue.run_follow_up_scan(scheduler.bot, chat_id)

        await queue.close()

        if drafts_sent > 0:
            logger.info(f"Follow-up scan: {drafts_sent} draft(s) sent")
        else:
            logger.info("Follow-up scan: no follow-ups due this cycle")

    except Exception:
        logger.exception("Follow-up scan task failed")


# ------------------------------------------------------------------
# Weekly portfolio check
# ------------------------------------------------------------------

async def task_weekly_portfolio_check(scheduler: "Scheduler") -> None:
    """Weekly reminder (Mondays) to check for new DSC project onboarding.

    Fires daily at 8 AM CT but only sends on Mondays. On other days it's a no-op.
    """
    now_ct = datetime.now(CT)
    if now_ct.weekday() != 0:  # 0 = Monday
        return

    chat_id = _get_chat_id()
    if not chat_id or not scheduler.bot:
        return

    project_list = "\n".join(
        f"  • {info['name']}"
        for _key, info in sorted(PROJECTS.items(), key=lambda x: x[1]['number'])
    )
    msg = (
        "📋 <b>Weekly Portfolio Check</b>\n\n"
        "Morning boss — weekly reminder to check if any new projects "
        "have been onboarded to the DSC portfolio.\n\n"
        "<b>Current portfolio (12 projects):</b>\n"
        f"{project_list}\n\n"
        "Any additions or changes? Let me know and I'll get the folder "
        "structure and monitoring set up."
    )
    await _send_telegram(scheduler.bot, chat_id, msg)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_chat_id() -> Optional[int]:
    """Get the target chat ID from environment config."""
    if REPORT_CHAT_ID:
        try:
            return int(REPORT_CHAT_ID.split(",")[0].strip())
        except (ValueError, IndexError):
            pass

    # Fallback: try ALLOWED_CHAT_IDS
    import os
    raw = os.getenv("ALLOWED_CHAT_IDS", "")
    if raw:
        try:
            return int(raw.split(",")[0].strip())
        except (ValueError, IndexError):
            pass

    return None


# ------------------------------------------------------------------
# One-time weekend to-do list (Saturday Feb 28, 2026 at 6 AM CT)
# ------------------------------------------------------------------

WEEKEND_TODO_MESSAGE = """\
<b>\U0001f6e0\ufe0f Weekend System To-Do List \u2014 Saturday Feb 28</b>

<b>GOLIATH SYSTEM IMPROVEMENTS:</b>

1. <b>Build OAuth callback endpoint for Recall.ai</b> \u2014 Calendar auto-join is connected in the Recall.ai dashboard but NOT through our API user. Need a small OAuth flow on the webhook server so meetings auto-record without manual /join commands. This is the last piece for full automation.

2. <b>Test transcript \u2192 ConstraintsPro auto-pipeline</b> \u2014 Pipeline is built and committed but hasn\u2019t been tested end-to-end through the normal webhook flow. Monday\u2019s first call will be the real test. Review the pipeline code for any edge cases.

3. <b>Clean up stale open action items</b> \u2014 34 open items in memory, ~8-10 are already completed (bot restart, Recall.ai commit, Scioto/Delta Bobcat syncs, Blackford report). Need to resolve completed items so the morning report to-do list stays clean and accurate.

4. <b>Fix PID file management</b> \u2014 start.sh isn\u2019t writing the correct PID on restart (showed 88569 when actual was 210396). Minor but annoying for health checks.

<b>MONDAY PREP \u2014 PROJECT ITEMS TO TRACK:</b>

\u2022 <b>Delta Bobcat:</b> James Kelly owes Shoals material reconciliation (Mon/Tue 3/2-3/3). Pile caps Kyan expedite still no response (~4 weeks). Ary gathering substation/JFE details. $5M tariff escalation to Mike Flynn tracking.

\u2022 <b>Scioto Ridge:</b> Luis emailing Gomez at Stantec re: DMC connectors (HIGH priority). Substation secondary power RFI blocked \u2014 RWE owes studies to Ohio Mid (~2 month delay). DC electrical starting Mon 3/2.

\u2022 <b>Blackford:</b> Probing questions report was generated \u2014 check if the meeting happened and if follow-up emails were sent.

<i>Today is Friday Feb 27, 2026. Happy weekend boss. \U0001f3af</i>\
"""


async def task_weekend_todo(scheduler: "Scheduler") -> None:
    """One-time weekend to-do list — fires at 6 AM CT on Saturday Feb 28, 2026.

    On any other day, this is a no-op. After successful delivery, the task
    disables itself so it never fires again.
    """
    now_ct = datetime.now(CT)

    # Only fire on Saturday Feb 28, 2026
    if now_ct.date() != datetime(2026, 2, 28).date():
        return

    chat_id = _get_chat_id()
    if not chat_id or not scheduler.bot:
        logger.warning("Weekend todo: no chat_id or bot available")
        return

    logger.info("Sending weekend to-do list...")
    await _send_telegram(scheduler.bot, chat_id, WEEKEND_TODO_MESSAGE)
    logger.info("Weekend to-do list delivered successfully")

    # Disable this task so it never fires again
    for task in scheduler._tasks:
        if task.name == "weekend_todo":
            task.enabled = False
            logger.info("Weekend todo task disabled (one-time delivery complete)")
            break


# ------------------------------------------------------------------
# Health Monitor Task (every 4 hours during business hours)
# ------------------------------------------------------------------

async def task_health_check(scheduler: "Scheduler") -> None:
    """Run the central health monitor and send results to Telegram.

    Executes health_monitor.py as a subprocess. The health monitor checks:
      - systemd service status (goliath-bot, goliath-web)
      - database freshness and integrity
      - disk usage
      - network health (web API, Cloudflare tunnel)
      - cron job output files
    """
    script_path = CRON_JOBS_DIR / "health_monitor.py"
    success, output = await _run_script_async(script_path, timeout=120)

    if success:
        logger.info(f"Health check completed:\n{output[:500]}")
    else:
        logger.warning(f"Health check reported issues:\n{output[:500]}")
        # The health_monitor.py script handles its own Telegram notification,
        # so we don't need to send anything here — just log the result.


async def task_token_health_check(scheduler: "Scheduler") -> None:
    """Check OAuth token health and proactively refresh if expiring soon.

    Runs every 30 minutes. If the token is within 2 hours of expiry,
    attempts to refresh it using the stored refresh token. If refresh
    fails, sends a Telegram alert with /reauth instructions.

    Added after the March 2, 2026 outage where an expired access token
    + Anthropic's refresh endpoint being down bricked the bot for 3+ hours.
    """
    from bot.services.token_health import get_token_health_monitor

    monitor = get_token_health_monitor()

    # Ensure the monitor has the bot reference for sending alerts
    if not monitor.bot and scheduler.bot:
        from bot.config import REPORT_CHAT_ID
        chat_id = int(REPORT_CHAT_ID) if REPORT_CHAT_ID else None
        monitor.set_bot(scheduler.bot, chat_id)

    try:
        await monitor.scheduled_check(scheduler=scheduler)
    except Exception as e:
        logger.error(f"Token health check failed: {e}", exc_info=True)


# ------------------------------------------------------------------
# Self-Improvement Tasks (V3 Experience Replay + V4 Prompt Review)
# ------------------------------------------------------------------

async def task_experience_replay(scheduler: "Scheduler") -> None:
    """2 AM CT daily — Extract lessons from low-scoring reflections.

    Runs the experience replay pipeline:
      1. Queries reflections with quality_score <= 3
      2. Runs heuristic pattern detectors (agent reliability, verbosity, over-dispatch, etc.)
      3. Generates lessons and stores them in lessons_learned table
      4. Lessons are injected into Nimrod prompts via get_lessons_for_prompt_injection()
    """
    from bot.memory.experience_replay import run_experience_replay

    try:
        lessons = await run_experience_replay()
        if lessons:
            logger.info(f"Experience replay: extracted {len(lessons)} new lesson(s)")
        else:
            logger.info("Experience replay: no new lessons (all reflections already analysed or above threshold)")
    except Exception as e:
        logger.error(f"Experience replay task failed: {e}", exc_info=True)


async def task_weekly_prompt_review(scheduler: "Scheduler") -> None:
    """3 AM CT Sunday — Heuristic audit of all agent prompts.

    Runs 5 parallel checks on each agent prompt:
      1. Length audit (too long/short)
      2. Staleness check (last git modification)
      3. Lesson integration (high-confidence lessons not yet applied)
      4. Effectiveness check (avg reflection score, dispatch frequency)
      5. Cross-reference with lessons_learned

    Results stored in prompt_reviews table for human approval.
    Findings surfaced in morning report.
    """
    from bot.memory.prompt_review import run_weekly_prompt_review

    try:
        summary = await run_weekly_prompt_review()
        logger.info(f"Weekly prompt review complete:\n{summary}")
    except Exception as e:
        logger.error(f"Weekly prompt review task failed: {e}", exc_info=True)


# ======================================================================
# Setup: register all default tasks and return a configured Scheduler
# ======================================================================

def create_scheduler(bot=None) -> Scheduler:
    """Create a Scheduler with all default GOLIATH tasks registered.

    Schedule philosophy:
      - Monday-Friday: Full workday schedule (5 AM morning report through 7 PM cleanup)
      - Saturday: Day off — only infrastructure tasks (email poll, heartbeat,
        daily constraints folder, daily scan)
      - Sunday: Shifted schedule — user-facing reports and sessions fire at 4 PM CT
        (respects church in the morning, preps for Monday in the evening)

    All tasks fire once per day at their specified CT time. The scheduler's
    three-layer defense (in-flight set, eager last_run, proactive dedup)
    ensures no task ever double-fires, even for long-running tasks like
    the morning report (can take several minutes with PDF generation).
    """
    sched = Scheduler(bot=bot)

    # ==================================================================
    # Mon-Fri workday tasks (standard schedule)
    # ==================================================================

    sched.add_task(
        name="morning_report",
        hour=5,
        minute=0,
        callback=task_morning_report,
        description="5 AM CT Mon-Fri — Morning report PDF with Bible verse, todo, health summary, scan results",
        days_of_week=MON_FRI,
    )

    sched.add_task(
        name="proactive_morning",
        hour=6,
        minute=0,
        callback=task_proactive_morning,
        description="6 AM CT Mon-Fri — Morning thinking session — Nimrod reviews memories and sends ideas",
        days_of_week=MON_FRI,
    )

    # DISABLED 2026-03-02 — redundant with morning report (user request)
    # Proactive Follow-Up: daily PDF report at 5 PM CT (Mon-Fri, end of day)
    # Moved from 7 AM to 5 PM on 2026-03-01 so the user gets ONE consolidated
    # PDF at end of day summarizing what to chase tomorrow morning.
    # Runs before the 6 PM evening proactive thinking session.
    # sched.add_task(
    #     name="proactive_followup_report",
    #     hour=17,
    #     minute=0,
    #     callback=task_proactive_followup_report,
    #     description="5 PM CT Mon-Fri — End-of-day Proactive Follow-Up PDF report (what to chase tomorrow)",
    #     days_of_week=MON_FRI,
    # )

    # DISABLED 2026-03-01: Follow-up queue scan sent 30+ individual Telegram
    # messages per run (one per follow-up item). Replaced by the consolidated
    # Proactive Follow-Up PDF report which generates ONE end-of-day PDF with
    # all constraint follow-ups organized by project and priority.
    # The followup.py module is kept for its commitment tracking DB and
    # get_overdue_summary() (used by the morning report), but its scheduled
    # scans that call send_for_approval() are disabled.
    #
    # for hour, minute in FOLLOWUP_SCAN_TIMES:
    #     time_label = f"{hour:02d}:{minute:02d}"
    #     sched.add_task(
    #         name=f"followup_{time_label}",
    #         hour=hour,
    #         minute=minute,
    #         callback=task_follow_up_scan,
    #         description=f"Follow-up scan at {time_label} CT Mon-Fri — check due commitments and approaching deadlines",
    #         days_of_week=MON_FRI,
    #     )

    sched.add_task(
        name="proactive_evening",
        hour=18,
        minute=0,
        callback=task_proactive_evening,
        description="6 PM CT Mon-Fri — Evening debrief — Nimrod reflects on the day and sets up tomorrow",
        days_of_week=MON_FRI,
    )

    sched.add_task(
        name="folder_cleanup",
        hour=19,
        minute=0,
        callback=task_folder_cleanup,
        description="7 PM CT Mon-Fri — Scan workspace for duplicates, misplaced files, and folder hygiene",
        days_of_week=MON_FRI,
    )

    # ==================================================================
    # Sunday shifted schedule (4 PM CT start — respects church morning)
    # ==================================================================

    sched.add_task(
        name="sunday_morning_report",
        hour=16,
        minute=0,
        callback=task_morning_report,
        description="4 PM CT Sunday — Morning report (Sunday edition, prep for Monday)",
        days_of_week=SUN_ONLY,
    )

    sched.add_task(
        name="sunday_proactive_morning",
        hour=16,
        minute=5,
        callback=task_proactive_morning,
        description="4:05 PM CT Sunday — Proactive thinking session (Sunday edition)",
        days_of_week=SUN_ONLY,
    )

    # DISABLED 2026-03-02 — redundant with morning report (user request)
    # sched.add_task(
    #     name="sunday_proactive_followup",
    #     hour=16,
    #     minute=15,
    #     callback=task_proactive_followup_report,
    #     description="4:15 PM CT Sunday — Proactive Follow-Up PDF report (Sunday edition)",
    #     days_of_week=SUN_ONLY,
    # )

    # DISABLED 2026-03-01: Sunday follow-up scan disabled along with the
    # Mon-Fri scans. Individual message spam replaced by consolidated PDF.
    # See the sunday_proactive_followup task at 4:15 PM for the PDF report.
    #
    # sched.add_task(
    #     name="sunday_followup_scan",
    #     hour=17,
    #     minute=0,
    #     callback=task_follow_up_scan,
    #     description="5 PM CT Sunday — Follow-up scan (Sunday edition)",
    #     days_of_week=SUN_ONLY,
    # )

    sched.add_task(
        name="sunday_proactive_evening",
        hour=18,
        minute=0,
        callback=task_proactive_evening,
        description="6 PM CT Sunday — Evening debrief (Sunday edition)",
        days_of_week=SUN_ONLY,
    )

    sched.add_task(
        name="sunday_folder_cleanup",
        hour=19,
        minute=0,
        callback=task_folder_cleanup,
        description="7 PM CT Sunday — Folder cleanup scan (Sunday edition)",
        days_of_week=SUN_ONLY,
    )

    # ==================================================================
    # Always-on tasks (every day including Saturday)
    # ==================================================================

    sched.add_task(
        name="daily_constraints_folder",
        hour=0,
        minute=5,
        callback=task_daily_constraints_folder,
        description="12:05 AM CT daily — Create date-stamped constraints folder for the new day",
    )

    # ==================================================================
    # Self-improvement tasks (V3/V4 — experience replay + prompt review)
    # ==================================================================

    sched.add_task(
        name="experience_replay",
        hour=2,
        minute=0,
        callback=task_experience_replay,
        description="2 AM CT daily — Extract lessons from low-scoring reflections (self-improvement V3)",
    )

    sched.add_task(
        name="weekly_prompt_review",
        hour=3,
        minute=0,
        callback=task_weekly_prompt_review,
        description="3 AM CT Sunday — Heuristic audit of all agent prompts (self-improvement V4)",
        days_of_week=SUN_ONLY,
    )

    sched.add_task(
        name="daily_scan",
        hour=23,
        minute=0,
        callback=task_daily_scan,
        description="11 PM CT daily — Run Claude analysis of POD/Schedule/Constraints across all projects",
    )

    sched.add_task(
        name="weekly_portfolio_check",
        hour=8,
        minute=0,
        callback=task_weekly_portfolio_check,
        description="Monday 8 AM CT — Remind about new DSC project onboarding",
        days_of_week={0},  # Monday only (has its own weekday guard too)
    )

    # Interval task: poll Gmail for inbound emails every 45 seconds
    sched.add_interval_task(
        name="email_poll",
        interval_seconds=45,
        callback=task_email_poll,
        description="Poll Gmail IMAP for inbound [INBOX:] tagged emails + auto-file POD/constraints attachments",
    )

    # DISABLED 2026-03-01: One-time task for Feb 28 — no longer needed.
    # Was missing days_of_week filter so it fired EVERY DAY at 6 AM.
    # Removed to prevent stale task execution.
    #
    # sched.add_task(
    #     name="weekend_todo",
    #     hour=6,
    #     minute=0,
    #     callback=task_weekend_todo,
    #     description="One-time weekend to-do list — Saturday Feb 28, 2026 at 6 AM CT",
    # )

    # ==================================================================
    # Health monitoring (every 4 hours during business hours)
    # ==================================================================
    # Runs at :30 past the hour to avoid colliding with other tasks
    # that fire on the hour (morning report, proactive sessions, etc.)

    for hour in [6, 10, 14, 18, 22]:
        sched.add_task(
            name=f"health_check_{hour:02d}",
            hour=hour,
            minute=30,
            callback=task_health_check,
            description=f"Health check at {hour}:30 CT — monitor services, DBs, disk, network",
        )

    # Interval task: OAuth token health check every 30 minutes
    # Proactively refreshes the access token before it expires.
    # Added after March 2, 2026 outage where expired token + Anthropic 503
    # bricked the bot for 3+ hours.
    sched.add_interval_task(
        name="token_health_check",
        interval_seconds=1800,  # 30 minutes
        callback=task_token_health_check,
        description="OAuth token health check — proactive refresh before expiry, alerts on failure",
    )

    # Interval task: Recall.ai transcript poller every 2 minutes
    # Catches transcripts from bots that lost their in-memory polling task
    # (e.g., bot restart, bots scheduled via API/email outside normal handler).
    # Uses a persistent JSON tracker for dedup so restarts never reprocess.
    # Added after the 2026-03-02 Scioto Ridge incident where the bot was
    # scheduled outside the normal handler and the transcript went unprocessed.
    from bot.services.recall_transcript_poller import (
        task_recall_transcript_poll,
        POLL_INTERVAL_SECONDS,
    )
    sched.add_interval_task(
        name="recall_transcript_poll",
        interval_seconds=POLL_INTERVAL_SECONDS,
        callback=task_recall_transcript_poll,
        description="Poll Recall.ai bots for completed transcripts (dedup-protected, survives restarts)",
    )

    return sched
