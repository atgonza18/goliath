#!/usr/bin/env python3
"""
Health Check Registration — Documents and provides the scheduler integration.

This file contains:
  1. The async task callback for the health check (used by the scheduler)
  2. Documentation of how it's registered in create_scheduler()

The health check runs:
  - Every 4 hours during business hours (6 AM - 10 PM CT)
  - Schedule: 6 AM, 10 AM, 2 PM, 6 PM, 10 PM CT (Mon-Sun)
  - Additionally runs at bot startup (see main.py modification)

The health check task is registered in scheduler.py's create_scheduler()
function. See the "Health monitoring" section in that function.

Integration was done by:
  1. Adding task_health_check() callback to scheduler.py
  2. Registering 5 daily tasks at 6 AM, 10 AM, 2 PM, 6 PM, 10 PM
  3. Adding startup self-test call in main.py's post_init()
"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
CT = ZoneInfo("America/Chicago")

REPO_ROOT = Path(__file__).resolve().parent.parent
CRON_JOBS_DIR = REPO_ROOT / "cron-jobs"


async def task_health_check(scheduler) -> None:
    """Scheduler callback: Run the health monitor and send results to Telegram.

    This is the async wrapper called by the bot's internal scheduler.
    It runs health_monitor.py as a subprocess to avoid importing its
    dependencies into the bot's event loop.
    """
    logger.info("Health check: starting...")

    script_path = CRON_JOBS_DIR / "health_monitor.py"
    if not script_path.exists():
        logger.error(f"Health check script not found: {script_path}")
        return

    try:
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(script_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(REPO_ROOT),
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=120  # 2 minute timeout
        )

        stdout_text = stdout.decode(errors="replace").strip()
        stderr_text = stderr.decode(errors="replace").strip()

        if process.returncode == 0:
            logger.info(f"Health check completed successfully:\n{stdout_text[:500]}")
        else:
            logger.warning(
                f"Health check returned non-zero ({process.returncode}):\n"
                f"stdout: {stdout_text[:300]}\nstderr: {stderr_text[:300]}"
            )

    except asyncio.TimeoutError:
        logger.error("Health check timed out after 120s")
    except Exception:
        logger.exception("Health check failed")


# ======================================================================
# Example: How to manually register in create_scheduler()
# ======================================================================
#
# The following code has been added to scheduler.py's create_scheduler():
#
#     from cron_jobs.register_health_check import task_health_check
#
#     # Health monitoring: every 4 hours during business hours (6 AM - 10 PM CT)
#     for hour in [6, 10, 14, 18, 22]:
#         sched.add_task(
#             name=f"health_check_{hour:02d}",
#             hour=hour,
#             minute=30,
#             callback=task_health_check,
#             description=f"Health check at {hour}:30 CT -- monitor services, DBs, disk, network",
#         )
#
# Note: The health check runs at :30 past the hour to avoid colliding with
# other tasks that fire on the hour (morning report at 5:00, proactive at 6:00, etc.)
