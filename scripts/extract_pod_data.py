#!/usr/bin/env python3
"""
Extract structured production data from POD PDFs using Claude.

Pipeline:
  1. Repair PDF if needed (strip null prefix from Power Automate corruption)
  2. Text extraction via pdfminer.six
  3. If text is empty/minimal, render PDF page to PNG via PyMuPDF and use vision
  4. Send to Claude API (with CLI fallback) for structured extraction
  5. Parse JSON response and store in SQLite

Usage:
    python3 scripts/extract_pod_data.py                          # process all un-extracted
    python3 scripts/extract_pod_data.py --file path/to/pod.pdf   # process single file
    python3 scripts/extract_pod_data.py --project blackford      # process one project
    python3 scripts/extract_pod_data.py --reprocess              # re-extract all (ignore log)
    python3 scripts/extract_pod_data.py --repair                 # strip null prefix from all PODs
    python3 scripts/extract_pod_data.py --status                 # show extraction status summary
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "telegram-bot"))

from bot.config import PROJECTS, PROJECTS_DIR

# Database path
DB_PATH = REPO_ROOT / "web-platform" / "backend" / "data" / "pod_production.db"

# Minimum text length to consider text extraction successful
MIN_TEXT_LENGTH = 100

# Maximum UTF-8 replacement chars (% of file) before marking unrepairable
MAX_CORRUPTION_PCT = 70.0

EXTRACTION_PROMPT = """You are analyzing a Plan of the Day (POD) / Daily Production Report from a solar construction site.

Extract EVERY production activity row individually from this document. Return ONLY valid JSON (no markdown fences, no explanation).

Required JSON structure:
{
  "report_date": "YYYY-MM-DD",
  "contractor_format": "white_construction" | "mastec" | "unknown",
  "activities": [
    {
      "activity_category": "<parent group>",
      "activity_name": "<specific line item exactly as shown>",
      "qty_to_date": <cumulative quantity completed to date, number or null>,
      "qty_last_workday": <value from prior day column, number or null>,
      "qty_completed_yesterday": <daily production quantity completed yesterday, number or 0>,
      "total_qty": <total scope/target quantity, number or null>,
      "unit": "<EA, LF, Block, etc. or null>",
      "pct_complete": <percentage 0-100, number or null>,
      "today_location": "<text from TODAY column describing work plan/location, or null>",
      "notes": "<status text like On Hold, Completed, Rained Out, or null>"
    }
  ]
}

CRITICAL INSTRUCTIONS:
- Extract EVERY row individually. Do NOT consolidate, aggregate, or merge sub-activities.
- activity_category = parent group heading (e.g., "Array Construction", "Electrical", "Civil", "Tracker Installation", "Module Installation")
- activity_name = specific line item exactly as written (e.g., "Tracker Pile Installation", "DC Trenching", "MV Cable Pulling")
- qty_to_date = the "QTY TO DATE" or cumulative quantity column. This is NOT today's production — it is all-time cumulative.
- qty_last_workday = the value from the previous day column. For White Construction PODs this is cumulative; for MasTec PODs it may be a daily increment.
- qty_completed_yesterday = the daily production quantity from the "Completed Yesterday", "Qty Yesterday", "Yesterday's Production", or similar column. This is a DIRECT field — the field team reports daily production explicitly. It is NOT a computed delta from cumulative totals. If the cell is blank or empty, use 0. A blank cell means ZERO production for that activity that day, not missing data.
- total_qty = total scope / target quantity for the entire project
- pct_complete = percentage complete (often qty_to_date / total_qty * 100)
- today_location = the text from the "TODAY" column. This is a work plan or location description, NOT a number.
- Handle "#DIV/0!" or "N/A" as null
- Parse comma-separated numbers (e.g., "39,653" → 39653)
- If a row has dual-crew quantities (e.g., "Crew A: 150 / Crew B: 200"), extract as a single row with the combined total
- Only extract rows that have at least one numeric value. Skip header rows and completely empty rows.
- Only extract data explicitly stated in the document. Do not fabricate or estimate values.
- If you cannot determine the report date, use null.
- IMPORTANT: For qty_completed_yesterday, blank/empty cells MUST be 0, not null. Only use null if the column itself does not exist in the document."""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def init_db():
    """Initialize the SQLite database with required tables.

    Auto-migrates from old schema (detects 'quantity_today' column) by
    dropping the production table and clearing extraction log to force
    re-extraction.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    # Auto-migration: detect old schema and drop if needed
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pod_production)").fetchall()]
        if "quantity_today" in cols:
            print("  [migrate] Old schema detected (quantity_today column). Dropping table and clearing log...")
            conn.execute("DROP TABLE IF EXISTS pod_production")
            conn.execute("DELETE FROM pod_extraction_log")
            conn.commit()
        # Add qty_completed_yesterday column if missing (non-destructive migration)
        elif "qty_completed_yesterday" not in cols and len(cols) > 0:
            print("  [migrate] Adding qty_completed_yesterday column...")
            conn.execute("ALTER TABLE pod_production ADD COLUMN qty_completed_yesterday REAL DEFAULT 0")
            conn.commit()
    except Exception:
        pass  # Table doesn't exist yet — fine

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pod_production (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_key TEXT NOT NULL,
            report_date TEXT NOT NULL,
            activity_category TEXT NOT NULL DEFAULT 'General',
            activity_name TEXT NOT NULL,
            qty_to_date REAL,
            qty_last_workday REAL,
            qty_completed_yesterday REAL DEFAULT 0,
            total_qty REAL,
            unit TEXT,
            pct_complete REAL,
            today_location TEXT,
            notes TEXT,
            extracted_at TEXT NOT NULL,
            source_file TEXT NOT NULL,
            UNIQUE(project_key, report_date, activity_category, activity_name)
        );

        CREATE TABLE IF NOT EXISTS pod_extraction_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL UNIQUE,
            project_key TEXT NOT NULL,
            report_date TEXT,
            status TEXT NOT NULL,
            error_message TEXT,
            activities_count INTEGER DEFAULT 0,
            extracted_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prod_project_date
            ON pod_production(project_key, report_date);

        CREATE INDEX IF NOT EXISTS idx_prod_date
            ON pod_production(report_date);

        CREATE INDEX IF NOT EXISTS idx_log_project
            ON pod_extraction_log(project_key);
    """)
    conn.close()


# ---------------------------------------------------------------------------
# PDF corruption detection and repair
# ---------------------------------------------------------------------------

def detect_corruption(pdf_path: Path) -> dict:
    """Analyze a PDF file for corruption. Returns a diagnostic dict.

    Returns:
        {
            "has_null_prefix": bool,
            "replacement_count": int,     # Number of U+FFFD chars (EF BF BD)
            "corruption_pct": float,      # % of file that is replacement chars
            "starts_with_pdf": bool,      # After any null stripping
            "file_size": int,
            "level": "clean" | "null_prefix" | "minor" | "severe" | "unreadable",
        }
    """
    try:
        data = pdf_path.read_bytes()
    except Exception:
        return {
            "has_null_prefix": False,
            "replacement_count": 0,
            "corruption_pct": 100.0,
            "starts_with_pdf": False,
            "file_size": 0,
            "level": "unreadable",
        }

    has_null = data[:4] == b"null"
    effective = data[4:] if has_null else data
    starts_with_pdf = effective[:5] == b"%PDF-"
    repl_count = data.count(b"\xef\xbf\xbd")
    pct = (repl_count * 3 / len(data)) * 100 if data else 0

    if not starts_with_pdf and not has_null:
        level = "unreadable"
    elif repl_count == 0:
        level = "null_prefix" if has_null else "clean"
    elif pct < 5:
        level = "minor"
    elif pct < MAX_CORRUPTION_PCT:
        level = "severe"
    else:
        level = "unreadable"

    return {
        "has_null_prefix": has_null,
        "replacement_count": repl_count,
        "corruption_pct": round(pct, 1),
        "starts_with_pdf": starts_with_pdf,
        "file_size": len(data),
        "level": level,
    }


def repair_pdf_on_disk(pdf_path: Path) -> bool:
    """Strip null prefix from a PDF file on disk. Returns True if repaired."""
    data = pdf_path.read_bytes()
    if data[:4] == b"null":
        pdf_path.write_bytes(data[4:])
        return True
    return False


def repair_all_pod_pdfs() -> dict:
    """Scan all project pod/ directories and strip null prefixes.

    Returns summary: {"total": int, "repaired": int, "already_clean": int}
    """
    total = 0
    repaired = 0
    for key in PROJECTS:
        pod_dir = PROJECTS_DIR / key / "pod"
        if not pod_dir.is_dir():
            continue
        for pdf in pod_dir.glob("*.pdf"):
            total += 1
            if repair_pdf_on_disk(pdf):
                repaired += 1
                print(f"  Repaired: {key}/{pdf.name}")

    already_clean = total - repaired
    print(f"\nRepair summary: {total} total, {repaired} repaired, {already_clean} already clean")
    return {"total": total, "repaired": repaired, "already_clean": already_clean}


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> str | None:
    """Extract text from PDF using pdfminer.six."""
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(str(pdf_path))
        if text and len(text.strip()) >= MIN_TEXT_LENGTH:
            return text.strip()
        return None
    except Exception as e:
        print(f"    pdfminer extraction failed: {e}")
        return None


def render_pdf_to_png(pdf_path: Path, output_dir: Path) -> list[Path]:
    """Render PDF pages to PNG images using PyMuPDF (fitz).

    Handles partially-corrupted PDFs gracefully — renders whatever pages
    are recoverable and skips pages that fail.
    """
    try:
        import fitz
    except ImportError:
        print("    PyMuPDF not installed — cannot render PDF to image")
        return []

    images = []
    try:
        doc = fitz.open(str(pdf_path))
        if len(doc) == 0:
            print("    PyMuPDF: PDF has 0 pages")
            doc.close()
            return []

        # Render first 3 pages max (PODs are typically 1-2 pages)
        for page_num in range(min(3, len(doc))):
            try:
                page = doc[page_num]
                # Render at 2x resolution for better readability
                mat = fitz.Matrix(2.0, 2.0)
                pix = page.get_pixmap(matrix=mat)
                img_path = output_dir / f"page_{page_num + 1}.png"
                pix.save(str(img_path))

                # Check if the rendered image has actual content.
                # Corrupted PDFs often render as solid white — detect this
                # by sampling pixel values.
                has_content = False
                try:
                    samples = pix.samples
                    # Sample pixels across the page; if we find any non-white
                    # pixel (value != 255), the page has visible content.
                    step = max(1, len(samples) // 2000)
                    unique = set()
                    for idx in range(0, min(len(samples), 20000), step):
                        unique.add(samples[idx])
                        if len(unique) > 2:
                            has_content = True
                            break
                    if len(unique) > 1:
                        has_content = True
                except Exception:
                    # If sampling fails, fall back to file size heuristic
                    img_size = img_path.stat().st_size
                    has_content = img_size > 50000  # 50KB+ likely has real content

                if has_content:
                    images.append(img_path)
                    img_size = img_path.stat().st_size
                    print(f"    Rendered page {page_num + 1}: {pix.width}x{pix.height} ({img_size:,} bytes)")
                else:
                    print(f"    Page {page_num + 1} rendered but is blank (solid white)")
                    img_path.unlink(missing_ok=True)

            except Exception as e:
                err = str(e)[:80]
                print(f"    Page {page_num + 1} render failed: {err}")

        doc.close()
    except Exception as e:
        print(f"    PDF open/render failed: {e}")

    return images


# ---------------------------------------------------------------------------
# Claude API / CLI calls
# ---------------------------------------------------------------------------

def _get_anthropic_client():
    """Get an Anthropic client instance.

    Tries ANTHROPIC_API_KEY env var first, then falls back to Claude's
    OAuth token from ~/.claude/.credentials.json (Claude Max subscription).
    """
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            # Read OAuth token from Claude credentials
            creds_path = Path.home() / ".claude" / ".credentials.json"
            if creds_path.exists():
                try:
                    creds = json.loads(creds_path.read_text())
                    api_key = creds.get("claudeAiOauth", {}).get("accessToken")
                except Exception:
                    pass
        if api_key:
            return anthropic.Anthropic(api_key=api_key)
        return None
    except Exception:
        return None


def call_claude_text(text: str, pdf_filename: str) -> dict | None:
    """Send extracted text to Anthropic API for structured extraction."""
    prompt = f"{EXTRACTION_PROMPT}\n\nDocument filename: {pdf_filename}\n\nExtracted text:\n{text[:30000]}"

    client = _get_anthropic_client()
    if client:
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16384,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.content[0].text
            result = parse_extraction_response(response_text)
            if result:
                return result
        except Exception as e:
            print(f"    Anthropic API call failed: {e}")

    # Fallback to CLI
    return _call_claude_cli_text(text, pdf_filename)


def _call_claude_cli_text(text: str, pdf_filename: str) -> dict | None:
    """Fallback: send extracted text to Claude CLI."""
    prompt = f"{EXTRACTION_PROMPT}\n\nDocument filename: {pdf_filename}\n\nExtracted text:\n{text[:30000]}"
    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=120, env=env,
        )
        if result.returncode != 0:
            print(f"    Claude CLI failed: {result.stderr[:200]}")
            return None
        try:
            outer = json.loads(result.stdout)
            response_text = outer.get("result", result.stdout)
        except json.JSONDecodeError:
            response_text = result.stdout
        return parse_extraction_response(response_text)
    except FileNotFoundError:
        print("    Claude CLI not found in PATH")
        return None
    except Exception as e:
        print(f"    Claude CLI fallback failed: {e}")
        return None


def call_claude_vision(image_paths: list[Path], pdf_filename: str) -> dict | None:
    """Send PDF page images to Anthropic API for vision-based extraction."""
    import base64 as b64

    client = _get_anthropic_client()
    if client:
        try:
            content = []
            for img_path in image_paths:
                img_data = b64.standard_b64encode(img_path.read_bytes()).decode("ascii")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_data,
                    },
                })
            content.append({
                "type": "text",
                "text": f"{EXTRACTION_PROMPT}\n\nDocument filename: {pdf_filename}\n\nThe document pages are provided as images above.",
            })

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=16384,
                messages=[{"role": "user", "content": content}],
            )
            response_text = response.content[0].text
            result = parse_extraction_response(response_text)
            if result:
                return result
        except Exception as e:
            print(f"    Anthropic API vision call failed: {e}")

    # Fallback: CLI with image descriptions (limited but better than nothing)
    return _call_claude_cli_vision(image_paths, pdf_filename)


def _call_claude_cli_vision(image_paths: list[Path], pdf_filename: str) -> dict | None:
    """Fallback: attempt vision extraction via Claude CLI.

    Claude CLI doesn't support inline images, but we can try rendering
    descriptions. This is a last resort.
    """
    # Claude CLI doesn't support vision — return None
    # The main call path already tried API; if API fails we can't do vision via CLI
    return None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_extraction_response(text: str) -> dict | None:
    """Parse Claude's response to extract the JSON structure."""
    if not text:
        return None

    # Try direct JSON parse
    text = text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and "activities" in data:
            return data
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    match = re.search(r'\{[\s\S]*"activities"[\s\S]*\}', text)
    if match:
        try:
            data = json.loads(match.group())
            if isinstance(data, dict) and "activities" in data:
                return data
        except json.JSONDecodeError:
            pass

    print(f"    Could not parse extraction response (first 200 chars): {text[:200]}")
    return None


def infer_date_from_filename(filename: str) -> str | None:
    """Try to extract a date from the filename (YYYY-MM-DD prefix)."""
    match = re.match(r'(\d{4}-\d{2}-\d{2})', filename)
    if match:
        return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def process_pdf(pdf_path: Path, project_key: str, conn: sqlite3.Connection) -> bool:
    """Process a single POD PDF and store extracted data.

    No longer skips corrupted files — tries every extraction method available.
    Returns True if data was successfully extracted and stored.
    """
    filename = pdf_path.name
    now = datetime.now(CT).isoformat()

    print(f"\n  Processing: {filename}")

    # Step 0: Detect and report corruption level
    corruption = detect_corruption(pdf_path)
    level = corruption["level"]

    if level == "unreadable":
        print(f"    UNREADABLE: file is not a valid PDF (no %PDF- header)")
        conn.execute(
            """INSERT OR REPLACE INTO pod_extraction_log
               (source_file, project_key, report_date, status, error_message, extracted_at)
               VALUES (?, ?, ?, 'corrupted', ?, ?)""",
            (str(pdf_path), project_key, infer_date_from_filename(filename),
             f"Not a valid PDF. Corruption: {corruption['corruption_pct']}%, "
             f"Replacement chars: {corruption['replacement_count']:,}", now),
        )
        conn.commit()
        return False

    if corruption["has_null_prefix"]:
        print(f"    Repairing: stripping null prefix...")
        repair_pdf_on_disk(pdf_path)
        corruption = detect_corruption(pdf_path)

    if level != "clean":
        print(f"    Corruption detected: {corruption['corruption_pct']}% "
              f"({corruption['replacement_count']:,} replacement chars) — "
              f"level: {level}")

    # Step 1: Try text extraction
    text = extract_text_from_pdf(pdf_path)

    extraction_data = None

    if text:
        print(f"    Text extracted: {len(text)} chars")
        extraction_data = call_claude_text(text, filename)
    else:
        print(f"    No text extracted — trying image-based extraction...")
        with tempfile.TemporaryDirectory() as tmpdir:
            images = render_pdf_to_png(pdf_path, Path(tmpdir))
            if images:
                extraction_data = call_claude_vision(images, filename)
            else:
                print(f"    Could not render any usable PDF pages")

    if not extraction_data:
        # Build descriptive error message
        if corruption["level"] in ("severe", "minor"):
            error = (
                f"PDF is corrupted by Power Automate relay "
                f"({corruption['corruption_pct']}% UTF-8 replacement chars). "
                f"Text extraction and image rendering both failed. "
                f"Fix: re-download clean PDF from Office 365 or fix PA flow."
            )
        else:
            error = "Claude extraction returned no parseable data"

        print(f"    FAILED: {error}")
        conn.execute(
            """INSERT OR REPLACE INTO pod_extraction_log
               (source_file, project_key, report_date, status, error_message, extracted_at)
               VALUES (?, ?, ?, 'failed', ?, ?)""",
            (str(pdf_path), project_key, infer_date_from_filename(filename), error, now),
        )
        conn.commit()
        return False

    # Determine report date
    report_date = extraction_data.get("report_date")
    if not report_date:
        report_date = infer_date_from_filename(filename)
    if not report_date:
        report_date = datetime.now(CT).strftime("%Y-%m-%d")

    activities = extraction_data.get("activities", [])
    print(f"    Extracted {len(activities)} activity rows for date {report_date}")

    # Store each activity row directly — no aggregation
    stored = 0
    for act in activities:
        category = act.get("activity_category") or "General"
        name = act.get("activity_name") or act.get("activity") or "Unknown"
        try:
            # qty_completed_yesterday: blank/empty = 0 (not null).
            # This is a direct field from the POD, not a computed delta.
            raw_yesterday = act.get("qty_completed_yesterday")
            qty_yesterday = 0 if raw_yesterday is None or raw_yesterday == "" else raw_yesterday

            conn.execute(
                """INSERT OR REPLACE INTO pod_production
                   (project_key, report_date, activity_category, activity_name,
                    qty_to_date, qty_last_workday, qty_completed_yesterday, total_qty, unit,
                    pct_complete, today_location, notes,
                    extracted_at, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_key,
                    report_date,
                    category,
                    name,
                    act.get("qty_to_date"),
                    act.get("qty_last_workday"),
                    qty_yesterday,
                    act.get("total_qty"),
                    act.get("unit"),
                    act.get("pct_complete") or act.get("percent_complete"),
                    act.get("today_location"),
                    act.get("notes"),
                    now,
                    str(pdf_path),
                ),
            )
            stored += 1
        except sqlite3.IntegrityError:
            pass

    # Log success
    conn.execute(
        """INSERT OR REPLACE INTO pod_extraction_log
           (source_file, project_key, report_date, status, activities_count, extracted_at)
           VALUES (?, ?, ?, 'success', ?, ?)""",
        (str(pdf_path), project_key, report_date, stored, now),
    )
    conn.commit()

    for act in activities:
        cat = act.get("activity_category") or ""
        name = act.get("activity_name") or act.get("activity") or "?"
        qty = act.get("qty_to_date")
        total = act.get("total_qty")
        unit = act.get("unit", "")
        pct = act.get("pct_complete") or act.get("percent_complete")
        parts = [f"    - [{cat}] {name}"]
        if qty is not None:
            parts.append(f": {qty:,.0f}")
            if total is not None:
                parts.append(f"/{total:,.0f}")
            parts.append(f" {unit}")
        if pct is not None:
            parts.append(f" ({pct}%)")
        print("".join(parts))

    return True


def get_unprocessed_pdfs(conn: sqlite3.Connection, project_filter: str | None = None) -> list[tuple[Path, str]]:
    """Find POD PDFs that haven't been extracted yet."""
    # Get already-processed files
    rows = conn.execute("SELECT source_file FROM pod_extraction_log WHERE status = 'success'").fetchall()
    processed = {row[0] for row in rows}

    results = []
    projects = [project_filter] if project_filter else list(PROJECTS.keys())

    for key in projects:
        pod_dir = PROJECTS_DIR / key / "pod"
        if not pod_dir.is_dir():
            continue
        for pdf in sorted(pod_dir.glob("*.pdf")):
            if str(pdf) not in processed:
                results.append((pdf, key))

    return results


def print_status():
    """Print extraction status summary."""
    conn = sqlite3.connect(str(DB_PATH))

    # Overall stats
    total_prod = conn.execute("SELECT COUNT(*) FROM pod_production").fetchone()[0]
    total_log = conn.execute("SELECT COUNT(*) FROM pod_extraction_log").fetchone()[0]
    success = conn.execute("SELECT COUNT(*) FROM pod_extraction_log WHERE status = 'success'").fetchone()[0]
    failed = conn.execute("SELECT COUNT(*) FROM pod_extraction_log WHERE status = 'failed'").fetchone()[0]
    corrupted = conn.execute("SELECT COUNT(*) FROM pod_extraction_log WHERE status = 'corrupted'").fetchone()[0]

    print("=" * 60)
    print("POD Extraction Status")
    print("=" * 60)
    print(f"Production rows:     {total_prod}")
    print(f"Files processed:     {total_log}")
    print(f"  Success:           {success}")
    print(f"  Failed:            {failed}")
    print(f"  Corrupted:         {corrupted}")

    # Count unprocessed
    unprocessed = get_unprocessed_pdfs(conn)
    print(f"  Unprocessed:       {len(unprocessed)}")

    # Recent failures with error messages
    rows = conn.execute(
        """SELECT source_file, project_key, status, error_message, extracted_at
           FROM pod_extraction_log WHERE status != 'success'
           ORDER BY extracted_at DESC LIMIT 10"""
    ).fetchall()
    if rows:
        print(f"\nRecent failures:")
        for row in rows:
            filepath, proj, status, error, ts = row
            fname = Path(filepath).name
            print(f"  [{status:9s}] {proj}/{fname}")
            if error:
                print(f"             {error[:100]}")

    # Corruption scan
    print(f"\nCorruption scan:")
    total_pdfs = 0
    clean_count = 0
    null_count = 0
    mangled_count = 0
    for key in PROJECTS:
        pod_dir = PROJECTS_DIR / key / "pod"
        if not pod_dir.is_dir():
            continue
        for pdf in pod_dir.glob("*.pdf"):
            total_pdfs += 1
            c = detect_corruption(pdf)
            if c["level"] == "clean":
                clean_count += 1
            elif c["has_null_prefix"]:
                null_count += 1
            elif c["replacement_count"] > 0:
                mangled_count += 1

    print(f"  Total PDFs:    {total_pdfs}")
    print(f"  Clean:         {clean_count}")
    print(f"  Null prefix:   {null_count}")
    print(f"  UTF-8 mangled: {mangled_count}")

    if mangled_count > 0:
        print(f"\n  ⚠ {mangled_count} PDFs have UTF-8 corruption from Power Automate relay.")
        print(f"    These files cannot be repaired locally — binary data is lost.")
        print(f"    Fix: Update Power Automate flow to handle binary attachments correctly,")
        print(f"    or re-download clean PDFs from Office 365 via Microsoft Graph API.")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Extract production data from POD PDFs")
    parser.add_argument("--file", type=str, help="Process a single PDF file")
    parser.add_argument("--project", type=str, help="Process only one project")
    parser.add_argument("--reprocess", action="store_true", help="Re-extract all (ignore log)")
    parser.add_argument("--repair", action="store_true",
                        help="Strip null prefix from all POD PDFs (does NOT re-extract)")
    parser.add_argument("--status", action="store_true", help="Show extraction status summary")
    args = parser.parse_args()

    init_db()

    if args.status:
        print_status()
        return

    if args.repair:
        print("Repairing POD PDFs (stripping null prefixes)...")
        repair_all_pod_pdfs()
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    if args.file:
        # Single file mode
        pdf_path = Path(args.file).resolve()
        if not pdf_path.exists():
            print(f"ERROR: File not found: {pdf_path}")
            sys.exit(1)

        # Infer project from path
        project_key = None
        for key in PROJECTS:
            if key in str(pdf_path):
                project_key = key
                break
        if not project_key:
            project_key = "unknown"

        success = process_pdf(pdf_path, project_key, conn)
        conn.close()
        sys.exit(0 if success else 1)

    # Batch mode
    if args.reprocess:
        print("Re-processing all PDFs (clearing extraction log)...")
        conn.execute("DELETE FROM pod_extraction_log")
        conn.execute("DELETE FROM pod_production")
        conn.commit()

    pdfs = get_unprocessed_pdfs(conn, args.project)
    if not pdfs:
        print("No unprocessed POD PDFs found.")
        conn.close()
        return

    print(f"Found {len(pdfs)} unprocessed POD PDFs.\n")

    success_count = 0
    fail_count = 0

    for pdf_path, project_key in pdfs:
        print(f"\n[{project_key}] {pdf_path.name}")
        if process_pdf(pdf_path, project_key, conn):
            success_count += 1
        else:
            fail_count += 1

    conn.close()

    print("\n" + "=" * 60)
    print(f"Processed: {success_count + fail_count}")
    print(f"  Success: {success_count}")
    print(f"  Failed:  {fail_count}")
    print("Done.")


if __name__ == "__main__":
    main()
