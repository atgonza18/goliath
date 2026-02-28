"""
Async Background Scheduler — runs cron-style tasks inside the bot's event loop.

Replaces system crontab with a lightweight asyncio-based scheduler that:
  - Runs tasks at specific times in America/Chicago timezone
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

Scheduled tasks:
  - 12:05 AM CT — Create daily constraints folder
  - 5:00 AM CT  — Morning report (Bible verse + todo list + scan + trends to Telegram)
  - 6:00 AM CT  — Morning proactive thinking session
  - 10:00 AM CT — Follow-up queue scan (commitments + approaching deadlines)
  - 4:00 PM CT  — Follow-up queue scan (commitments + approaching deadlines)
  - 6:00 PM CT  — Evening proactive thinking session
  - 7:00 PM CT  — Folder cleanup scan
  - 11:00 PM CT — Daily scan (run Claude analysis of all 12 projects)
  - Every 45s  — Poll Gmail IMAP for inbound [INBOX:] tagged emails
"""

import asyncio
import logging
import os
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
    ESCALATION_DB_PATH, ESCALATION_SCAN_TIMES,
    HEARTBEAT_INTERVAL_SECONDS,
    FOLLOWUP_DB_PATH, FOLLOWUP_SCAN_TIMES,
)

logger = logging.getLogger(__name__)

CT = ZoneInfo("America/Chicago")

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
    ) -> None:
        """Register a new daily task."""
        task = ScheduledTask(
            name=name,
            hour=hour,
            minute=minute,
            callback=callback,
            description=description,
        )
        self._tasks.append(task)
        logger.info(
            f"Scheduler: registered '{name}' at {hour:02d}:{minute:02d} CT — {description}"
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
        result = []
        for t in self._tasks:
            if t.interval_seconds > 0:
                time_str = f"every {t.interval_seconds}s"
            else:
                time_str = f"{t.hour:02d}:{t.minute:02d}"
            result.append({
                "name": t.name,
                "time_ct": time_str,
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
    """5:00 AM CT — Assemble and send the morning report."""
    if not scheduler.bot:
        logger.error("Morning report: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Morning report: no chat ID configured (set REPORT_CHAT_ID)")
        return

    now_ct = datetime.now(CT)
    date_str = now_ct.strftime("%A, %B %d, %Y")

    # ---- Build the report sections ----
    sections = []

    # Header
    sections.append(
        f"<b>GOLIATH Morning Report</b>\n"
        f"<i>{date_str}</i>\n"
    )

    # Section 1: Today's Gospel Word
    verse = _get_daily_verse(now_ct)
    sections.append(
        f"\u271d\ufe0f <b>Today's Gospel Word</b>\n"
        f"<i>\"{verse['text']}\"</i>\n"
        f"— {verse['ref']}\n\n"
        f"\U0001f4ad <i>{verse['thought']}</i>"
    )

    # Section 2: Daily To-Do List
    todo_content = _get_latest_todo()
    if todo_content:
        todo_html = _markdown_to_html(todo_content)
        sections.append(
            f"<b>Daily To-Do List</b>\n"
            f"{todo_html}"
        )
    else:
        sections.append(
            f"<b>Daily To-Do List</b>\n"
            f"<i>No todo file found in /reports/. "
            f"Ask Nimrod to generate one.</i>"
        )

    # Section 3: Project Health Summary
    health = _build_project_health_summary()
    sections.append(
        f"<b>Portfolio Health Summary</b>\n"
        f"<i>{len(PROJECTS)} projects tracked</i>\n\n"
        f"{health}"
    )

    # Section 4: Latest Scan Report (condensed)
    scan_content = _get_latest_scan_report()
    if scan_content:
        # Convert from Markdown to HTML and truncate if very long
        scan_html = _markdown_to_html(scan_content)
        if len(scan_html) > 3000:
            scan_html = scan_html[:2900] + "\n\n<i>... (truncated — full report available in cron-jobs/reports/)</i>"
        sections.append(
            f"<b>Latest Daily Scan</b>\n"
            f"{scan_html}"
        )
    else:
        sections.append(
            f"<b>Latest Daily Scan</b>\n"
            f"<i>No scan reports found yet. The 11 PM scan has not run or produced output.</i>"
        )

    # Section 5: Constraint Movement (24h) + Follow-Up Queue (trend analysis)
    try:
        from bot.services.trend_analysis import TrendAnalyzer
        trend_analyzer = TrendAnalyzer()
        trend_section = await trend_analyzer.generate_trend_section()
        sections.append(trend_section)
    except Exception:
        logger.exception("Morning report: trend analysis section failed")
        sections.append(
            "<b>Constraint Movement (24h)</b>\n"
            "<i>Unable to analyze constraint changes at this time.</i>"
        )

    # Footer
    sections.append(
        "<i>Generated by GOLIATH Scheduler</i>"
    )

    full_report = "\n\n---\n\n".join(sections)

    await _send_telegram(scheduler.bot, chat_id, full_report)
    logger.info(f"Morning report sent to chat_id={chat_id} ({len(full_report)} chars)")

    # ---- Generate and send report file attachments (Markdown, Excel, PDF) ----
    await _generate_and_send_report_files(scheduler, chat_id, now_ct)


async def _generate_and_send_report_files(
    scheduler: "Scheduler", chat_id: int, now_ct: datetime
) -> None:
    """Generate Markdown, Excel, and PDF report files and send them as Telegram documents.

    Called after the text morning report is sent. Each generator is independent —
    if one fails, the others still get sent.
    """
    date_str = now_ct.strftime("%Y-%m-%d")
    files_sent = []
    files_failed = []

    # --- Step 1: Run the daily scan to produce the Markdown report ---
    logger.info("Morning report attachments: running daily scan for Markdown report...")
    scan_script = CRON_JOBS_DIR / "daily_scan.py"
    md_report_path = CRON_REPORTS_DIR / f"{date_str}_daily_scan.md"

    # Only run scan if today's report doesn't already exist
    if not md_report_path.exists():
        success, output = await _run_script_async(scan_script, timeout=720)
        if success:
            logger.info(f"Daily scan completed: {output[:200]}")
        else:
            logger.error(f"Daily scan failed: {output[:300]}")
    else:
        logger.info(f"Daily scan report already exists: {md_report_path}")

    # Find the most recent markdown scan report (in case date naming differs)
    if md_report_path.exists():
        actual_md_path = md_report_path
    else:
        md_candidates = sorted(CRON_REPORTS_DIR.glob("*_daily_scan.md"), reverse=True)
        actual_md_path = md_candidates[0] if md_candidates else None

    if actual_md_path and actual_md_path.exists():
        sent = await _send_telegram_document(
            scheduler.bot, chat_id, actual_md_path,
            caption=f"<b>Daily Scan Report</b> — {date_str}",
        )
        if sent:
            files_sent.append(actual_md_path.name)
        else:
            files_failed.append(("Markdown", str(actual_md_path)))
    else:
        logger.warning("No Markdown scan report found to send")
        files_failed.append(("Markdown", "no file found"))

    # --- Step 2: Run the Excel dashboard generator ---
    logger.info("Morning report attachments: generating Excel dashboard...")
    excel_script = REPORTS_DIR / "generate_dashboard.py"
    excel_report_path = REPORTS_DIR / f"{date_str}-portfolio-dashboard.xlsx"

    success, output = await _run_script_async(excel_script, timeout=120)
    if success:
        logger.info(f"Excel dashboard generated: {output[:200]}")
    else:
        logger.error(f"Excel dashboard generation failed: {output[:300]}")

    # Find the Excel file (the script may use a slightly different name)
    if excel_report_path.exists():
        actual_xlsx_path = excel_report_path
    else:
        xlsx_candidates = sorted(REPORTS_DIR.glob(f"*portfolio-dashboard*.xlsx"), reverse=True)
        actual_xlsx_path = xlsx_candidates[0] if xlsx_candidates else None

    if actual_xlsx_path and actual_xlsx_path.exists():
        sent = await _send_telegram_document(
            scheduler.bot, chat_id, actual_xlsx_path,
            caption=f"<b>Portfolio Dashboard (Excel)</b> — {date_str}",
        )
        if sent:
            files_sent.append(actual_xlsx_path.name)
        else:
            files_failed.append(("Excel", str(actual_xlsx_path)))
    else:
        logger.warning("No Excel dashboard found to send")
        files_failed.append(("Excel", "no file found"))

    # --- Step 3: Run the PDF report generator ---
    logger.info("Morning report attachments: generating PDF report...")
    pdf_script = REPORTS_DIR / "generate_pdf.py"
    pdf_report_path = REPORTS_DIR / f"{date_str}-portfolio-report.pdf"

    try:
        success, output = await _run_script_async(pdf_script, timeout=120)
        if success:
            logger.info(f"PDF report generated: {output[:200]}")
        else:
            logger.error(f"PDF report generation failed: {output[:300]}")
    except Exception as e:
        logger.error(f"PDF generation raised an exception: {e}")
        success = False

    # Find the PDF file (search for any recent portfolio PDF)
    if pdf_report_path.exists():
        actual_pdf_path = pdf_report_path
    else:
        pdf_candidates = sorted(REPORTS_DIR.glob(f"*portfolio*.pdf"), reverse=True)
        actual_pdf_path = pdf_candidates[0] if pdf_candidates else None

    if actual_pdf_path and actual_pdf_path.exists():
        sent = await _send_telegram_document(
            scheduler.bot, chat_id, actual_pdf_path,
            caption=f"<b>Portfolio Report (PDF)</b> — {date_str}",
        )
        if sent:
            files_sent.append(actual_pdf_path.name)
        else:
            files_failed.append(("PDF", str(actual_pdf_path)))
    else:
        logger.warning("No PDF report found to send")
        files_failed.append(("PDF", "no file generated or script missing"))

    # --- Summary log ---
    logger.info(
        f"Morning report attachments complete: "
        f"{len(files_sent)} sent ({', '.join(files_sent) if files_sent else 'none'}), "
        f"{len(files_failed)} failed ({', '.join(f[0] for f in files_failed) if files_failed else 'none'})"
    )

    # Send a brief status message if any files failed
    if files_failed and files_sent:
        fail_list = ", ".join(f[0] for f in files_failed)
        await _send_telegram(
            scheduler.bot, chat_id,
            f"<i>Note: Could not generate/send some report files: {fail_list}. "
            f"Successfully sent: {', '.join(files_sent)}.</i>"
        )
    elif files_failed and not files_sent:
        fail_list = "\n".join(f"  - {f[0]}: {f[1]}" for f in files_failed)
        await _send_telegram(
            scheduler.bot, chat_id,
            f"<i>Warning: No report file attachments could be generated.\n"
            f"<pre>{fail_list}</pre></i>"
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
            process.communicate(), timeout=720  # 12 minutes
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
        from bot.agents.runner import SubagentRunner
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
        runner = SubagentRunner()
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
# Escalation Engine tasks (9 AM, 1 PM, 5 PM CT)
# ------------------------------------------------------------------

async def task_escalation_scan(scheduler: "Scheduler") -> None:
    """Run escalation scan — pulls HIGH/MEDIUM constraints, drafts escalation emails,
    sends each to Telegram for approve/reject.

    Fires 3x/day at 9 AM, 1 PM, and 5 PM CT. Each constraint tracks its own
    escalation level and 5-day cooldown, so repeated scans won't re-escalate
    the same constraint until the cooldown expires.
    """
    if not scheduler.bot:
        logger.error("Escalation scan: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Escalation scan: no chat ID configured (set REPORT_CHAT_ID)")
        return

    try:
        from bot.services.escalation import EscalationTracker

        tracker = EscalationTracker(ESCALATION_DB_PATH)
        await tracker.initialize()

        drafts_sent = await tracker.run_escalation_scan(scheduler.bot, chat_id)

        await tracker.close()

        if drafts_sent > 0:
            logger.info(f"Escalation scan: {drafts_sent} draft(s) sent for approval")
        else:
            logger.info("Escalation scan: no escalations needed this cycle")

    except Exception:
        logger.exception("Escalation scan task failed")


# ------------------------------------------------------------------
# Constraint Heartbeat task (every 60 minutes)
# ------------------------------------------------------------------

async def task_constraint_heartbeat(scheduler: "Scheduler") -> None:
    """Hourly constraint heartbeat — snapshot ConstraintsPro state, compare to
    previous snapshot, notify user only if something meaningful changed.

    Silent if nothing changed (no spam).
    """
    if not scheduler.bot:
        logger.error("Constraint heartbeat: no bot instance")
        return

    chat_id = _get_chat_id()
    if not chat_id:
        logger.error("Constraint heartbeat: no chat ID configured (set REPORT_CHAT_ID)")
        return

    try:
        from bot.services.heartbeat import ConstraintHeartbeat

        heartbeat = ConstraintHeartbeat()
        changes = await heartbeat.run_heartbeat(scheduler.bot, chat_id)

        if changes > 0:
            logger.info(f"Constraint heartbeat: {changes} change(s) notified")
        else:
            logger.debug("Constraint heartbeat: no changes (silent)")

    except Exception:
        logger.exception("Constraint heartbeat task failed")


# ------------------------------------------------------------------
# Follow-Up Queue task (10 AM and 4 PM CT)
# ------------------------------------------------------------------

async def task_follow_up_scan(scheduler: "Scheduler") -> None:
    """Run follow-up queue scan — checks for due commitments and approaching
    constraint deadlines, drafts follow-up messages, and sends each to
    Telegram for approve/reject.

    Fires 2x/day at 10 AM and 4 PM CT, staggered from escalation scans.
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
            logger.info(f"Follow-up scan: {drafts_sent} draft(s) sent for approval")
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


# ======================================================================
# Setup: register all default tasks and return a configured Scheduler
# ======================================================================

def create_scheduler(bot=None) -> Scheduler:
    """Create a Scheduler with all default GOLIATH tasks registered.

    All tasks fire once per day at their specified CT time. The scheduler's
    three-layer defense (in-flight set, eager last_run, proactive dedup)
    ensures no task ever double-fires, even for long-running tasks like
    the morning report (can take 12+ minutes with attachment generation).
    """
    sched = Scheduler(bot=bot)

    sched.add_task(
        name="morning_report",
        hour=5,
        minute=0,
        callback=task_morning_report,
        description="Send morning report with Bible verse, todo list, health summary, scan results, and file attachments (MD, XLSX, PDF)",
    )

    sched.add_task(
        name="daily_scan",
        hour=23,
        minute=0,
        callback=task_daily_scan,
        description="Run Claude analysis of POD/Schedule/Constraints across all projects",
    )

    sched.add_task(
        name="daily_constraints_folder",
        hour=0,
        minute=5,
        callback=task_daily_constraints_folder,
        description="Create date-stamped constraints folder for the new day",
    )

    sched.add_task(
        name="folder_cleanup",
        hour=19,
        minute=0,
        callback=task_folder_cleanup,
        description="Scan workspace for duplicates, misplaced files, and folder hygiene issues",
    )

    sched.add_task(
        name="proactive_morning",
        hour=6,
        minute=0,
        callback=task_proactive_morning,
        description="6 AM CT morning thinking session — Nimrod reviews memories and sends ideas",
    )

    sched.add_task(
        name="proactive_evening",
        hour=18,
        minute=0,
        callback=task_proactive_evening,
        description="6 PM CT evening debrief — Nimrod reflects on the day and sets up tomorrow",
    )

    sched.add_task(
        name="weekly_portfolio_check",
        hour=8,
        minute=0,
        callback=task_weekly_portfolio_check,
        description="Monday 8 AM CT — Remind about new DSC project onboarding",
    )

    # Interval task: poll Gmail for inbound emails every 45 seconds
    sched.add_interval_task(
        name="email_poll",
        interval_seconds=45,
        callback=task_email_poll,
        description="Poll Gmail IMAP for inbound [INBOX:] tagged emails + auto-file POD/constraints attachments",
    )

    # Escalation engine: 3x/day at 9 AM, 1 PM, 5 PM CT
    for hour, minute in ESCALATION_SCAN_TIMES:
        time_label = f"{hour:02d}:{minute:02d}"
        sched.add_task(
            name=f"escalation_{time_label}",
            hour=hour,
            minute=minute,
            callback=task_escalation_scan,
            description=f"Escalation scan at {time_label} CT — draft follow-up emails for stalled constraints",
        )

    # Constraint heartbeat: every hour
    sched.add_interval_task(
        name="constraint_heartbeat",
        interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        callback=task_constraint_heartbeat,
        description="Hourly constraint snapshot + diff — notify on meaningful changes only",
    )

    # Follow-up queue: 2x/day at 10 AM and 4 PM CT
    for hour, minute in FOLLOWUP_SCAN_TIMES:
        time_label = f"{hour:02d}:{minute:02d}"
        sched.add_task(
            name=f"followup_{time_label}",
            hour=hour,
            minute=minute,
            callback=task_follow_up_scan,
            description=f"Follow-up scan at {time_label} CT — check due commitments and approaching deadlines",
        )

    # One-time weekend to-do list: Saturday Feb 28, 2026 at 6 AM CT
    sched.add_task(
        name="weekend_todo",
        hour=6,
        minute=0,
        callback=task_weekend_todo,
        description="One-time weekend to-do list — Saturday Feb 28, 2026 at 6 AM CT",
    )

    return sched
