from pathlib import Path

from bot.config import PROJECTS, PROJECTS_DIR, PROJECT_SUBFOLDERS


def _count_real_files(path: Path) -> int:
    """Count files excluding .gitkeep."""
    if not path.exists():
        return 0
    return sum(1 for f in path.rglob("*") if f.is_file() and f.name != ".gitkeep")


def get_portfolio_overview() -> str:
    """Generate a one-line-per-project status summary."""
    lines = ["*GOLIATH Portfolio Status*\n"]
    for key, info in PROJECTS.items():
        project_dir = PROJECTS_DIR / key
        file_count = _count_real_files(project_dir)
        constraints_count = _count_real_files(project_dir / "constraints")
        schedule_count = _count_real_files(project_dir / "schedule")
        pod_count = _count_real_files(project_dir / "pod")

        lines.append(
            f"{info['number']}. *{info['name']}* (`{key}`)\n"
            f"   Files: {file_count} | "
            f"Constraints: {constraints_count} | "
            f"Schedule: {schedule_count} | "
            f"POD: {pod_count}"
        )
    return "\n".join(lines)


def get_project_summary(project_key: str) -> str:
    """Detailed summary for a single project."""
    info = PROJECTS[project_key]
    project_dir = PROJECTS_DIR / project_key
    lines = [f"*{info['name']}* (#{info['number']})\n"]

    for subfolder in PROJECT_SUBFOLDERS:
        sub_path = project_dir / subfolder
        if sub_path.exists():
            files = [f for f in sub_path.iterdir() if f.is_file() and f.name != ".gitkeep"]
            if files:
                lines.append(f"*{subfolder}/*")
                for f in sorted(files):
                    size_kb = f.stat().st_size / 1024
                    lines.append(f"  - `{f.name}` ({size_kb:.1f} KB)")
            else:
                lines.append(f"*{subfolder}/* _(empty)_")

    return "\n".join(lines)


def list_project_files(project_key: str, subfolder: str | None = None) -> str:
    """List files in a project, optionally filtered to a subfolder."""
    project_dir = PROJECTS_DIR / project_key
    target = project_dir / subfolder if subfolder else project_dir

    if not target.exists():
        return f"Path not found: {target.relative_to(PROJECTS_DIR)}"

    files = sorted(f for f in target.rglob("*") if f.is_file() and f.name != ".gitkeep")
    if not files:
        return "No files found in this folder yet."

    lines = [f"*Files in {project_key}/{subfolder or ''}*\n"]
    for f in files:
        rel = f.relative_to(project_dir)
        size_kb = f.stat().st_size / 1024
        lines.append(f"  `{rel}` ({size_kb:.1f} KB)")
    return "\n".join(lines)


def read_project_file(project_key: str, relative_path: str) -> str:
    """Read a file from a project folder. Supports .md, .csv, .txt, .xlsx."""
    project_dir = PROJECTS_DIR / project_key
    file_path = (project_dir / relative_path).resolve()

    # Security: ensure the resolved path is still under projects/
    if not str(file_path).startswith(str(PROJECTS_DIR.resolve())):
        return "Error: Path traversal detected. Access denied."

    if not file_path.exists():
        return f"File not found: {relative_path}"

    suffix = file_path.suffix.lower()
    if suffix in (".md", ".txt", ".csv", ".json", ".log"):
        content = file_path.read_text(errors="replace")
        if len(content) > 15000:
            content = content[:15000] + "\n\n... (truncated, file too large for Telegram)"
        return f"*{file_path.name}*\n```\n{content}\n```"
    elif suffix in (".xlsx", ".xls"):
        return _read_excel_summary(file_path)
    elif suffix == ".pdf":
        return (
            f"PDF file: `{file_path.name}` "
            f"({file_path.stat().st_size / 1024:.1f} KB) — "
            f"PDF viewing not supported in chat. "
            f"Send me a plain-text question about it and I'll use Claude to analyze it."
        )
    else:
        return f"Unsupported file type: {suffix}"


def _read_excel_summary(file_path: Path) -> str:
    """Read an Excel file and return a text summary."""
    import pandas as pd

    try:
        df = pd.read_excel(file_path, engine="openpyxl")
        summary = f"*{file_path.name}* ({len(df)} rows, {len(df.columns)} columns)\n"
        summary += f"Columns: {', '.join(df.columns.astype(str))}\n\n"
        summary += f"```\n{df.head(20).to_string()}\n```"
        if len(df) > 20:
            summary += f"\n... ({len(df) - 20} more rows)"
        return summary
    except Exception as e:
        return f"Error reading Excel file: {e}"
