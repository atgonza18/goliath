#!/usr/bin/env python3
"""
Daily Project Scan — 6 PM CT
Crontab: 0 18 * * * cd /workspaces/goliath && /workspaces/goliath/cron-jobs/.venv/bin/python cron-jobs/daily_scan.py

Scans POD, Schedule, and Constraints for all 12 projects.
Generates a report with findings and questions for the site team.
Saves report to cron-jobs/reports/YYYY-MM-DD_daily_scan.md
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = REPO_ROOT / "projects"
REPORTS_DIR = REPO_ROOT / "cron-jobs" / "reports"
SCAN_FOLDERS = ["pod", "schedule", "constraints"]

PROJECTS = [
    "union-ridge", "duff", "salt-branch", "blackford",
    "delta-bobcat", "tehuacana", "three-rivers", "scioto-ridge",
    "mayes", "graceland", "pecan-prairie", "duffy-bess",
]


def collect_project_data() -> str:
    """Walk each project's POD, Schedule, Constraints folders and collect file info."""
    sections = []

    for project_key in PROJECTS:
        project_path = PROJECTS_DIR / project_key
        project_lines = [f"## {project_key}"]
        has_data = False

        for folder in SCAN_FOLDERS:
            folder_path = project_path / folder
            if not folder_path.exists():
                project_lines.append(f"### {folder}/\n_Folder missing_\n")
                continue

            files = [
                f for f in sorted(folder_path.rglob("*"))
                if f.is_file() and f.name != ".gitkeep"
            ]

            if not files:
                project_lines.append(f"### {folder}/\n_No data files_\n")
                continue

            has_data = True
            project_lines.append(f"### {folder}/ ({len(files)} file(s))")

            for fp in files:
                rel = fp.relative_to(project_path)
                size_kb = fp.stat().st_size / 1024
                project_lines.append(f"- `{rel}` ({size_kb:.1f} KB)")

                # Include content for small text files
                if fp.suffix in (".md", ".txt", ".csv") and fp.stat().st_size < 50_000:
                    try:
                        content = fp.read_text(errors="replace")
                        project_lines.append(f"```\n{content[:5000]}\n```")
                    except Exception:
                        pass

            project_lines.append("")

        if not has_data:
            project_lines.append("_No data files in POD, Schedule, or Constraints._\n")

        sections.append("\n".join(project_lines))

    return "\n---\n".join(sections)


def run_claude_scan(project_data: str) -> str:
    """Send collected data to Claude CLI for analysis and report generation."""
    prompt = f"""You are the GOLIATH DSC Operations Agent running a scheduled end-of-day scan.

Below is the current state of POD, Schedule, and Constraints data across all 12 projects.

Analyze this data and produce a report structured as follows:

# Daily Scan Report — {datetime.now().strftime('%B %d, %Y')}

## Executive Summary
(2-3 sentences on overall portfolio health)

## Findings by Project
For EACH project that has data, provide:
- **POD**: Production status, any variances vs plan
- **Schedule**: Float status, any erosion or compression risks
- **Constraints**: Active blockers, aging items, resolution status

Skip projects with no data files (just note "No data — awaiting upload").

## Portfolio-Level Concerns
(Cross-project risks, resource conflicts, weather impacts, etc.)

## Questions for Site Teams
For EACH project with findings, list 2-4 specific questions the DSC analyst should raise with that site team. These should be actionable, targeted questions about the data.

## Action Items
(Recommended next steps for the DSC analyst)

---

PROJECT DATA:
{project_data}
"""

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--output-format", "text",
        "--max-budget-usd", "0.50",
        prompt,
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(REPO_ROOT),
        env=env,
    )

    if result.returncode != 0:
        return f"# Scan Failed\n\n```\n{result.stderr[:2000]}\n```"

    return result.stdout.strip() if result.stdout.strip() else "# Scan returned empty response"


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"{today}_daily_scan.md"

    print(f"[{datetime.now().isoformat()}] Starting daily scan...")

    # Collect data from all projects
    print("Collecting project data...")
    project_data = collect_project_data()

    # Run Claude analysis
    print("Running Claude analysis...")
    report = run_claude_scan(project_data)

    # Save report
    report_path.write_text(report)
    print(f"Report saved: {report_path}")
    print(f"Report size: {len(report)} chars")

    return 0


if __name__ == "__main__":
    sys.exit(main())
