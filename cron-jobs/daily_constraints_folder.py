#!/usr/bin/env python3
"""
Daily Constraints Folder Creator — Midnight CT
Crontab: 0 0 * * * cd /opt/goliath && python cron-jobs/daily_constraints_folder.py

Creates a new date-stamped subfolder under dsc-constraints-production-reports/
each day so constraint logs, reports, and snapshots have a home before
the analyst or agents start their work.
"""

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DSC_REPORTS_DIR = REPO_ROOT / "dsc-constraints-production-reports"

PROJECTS = [
    "union-ridge", "duff", "salt-branch", "blackford",
    "delta-bobcat", "tehuacana", "three-rivers", "scioto-ridge",
    "mayes", "graceland", "pecan-prairie", "duffy-bess",
]


def create_daily_folder() -> Path:
    """Create today's date folder with a standard structure."""
    today = datetime.now().strftime("%Y-%m-%d")
    day_folder = DSC_REPORTS_DIR / today

    # Create the main date folder
    day_folder.mkdir(parents=True, exist_ok=True)

    # Create a daily index file so agents know what's expected
    index_path = day_folder / "index.txt"
    if not index_path.exists():
        index_path.write_text(
            f"DSC Constraints & Production Reports — {today}\n"
            f"{'=' * 50}\n\n"
            f"This folder was auto-created by the daily cron job.\n\n"
            f"Expected contents:\n"
            f"  - Constraint reports (PDF/TXT) per project\n"
            f"  - Action item lists for APM follow-up\n"
            f"  - Schedule risk snapshots\n"
            f"  - Production (POD) analysis if available\n\n"
            f"Projects in portfolio:\n"
            + "\n".join(f"  - {p}" for p in PROJECTS)
            + "\n"
        )

    return day_folder


def main():
    # Ensure the parent DSC reports directory exists
    DSC_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{datetime.now().isoformat()}] Creating daily constraints folder for {today}...")

    day_folder = create_daily_folder()

    print(f"Folder ready: {day_folder}")
    print(f"Contents: {list(day_folder.iterdir())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
