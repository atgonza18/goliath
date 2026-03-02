#!/usr/bin/env python3
"""
Generate a Follow-Up PDF report from raw constraint data with SPECIALIST BRAINS.

Reads constraints JSON from stdin or file, routes each constraint category to
the appropriate specialist (construction manager, cost analyst, scheduling expert)
for intelligent, solution-oriented follow-up drafts, then generates a professional PDF.

Architecture:
  1. Fast Convex data pull (pull_constraints_direct.mjs) provides the JSON
  2. Constraints are categorized: CONSTRUCTION, PROCUREMENT, ENGINEERING, PERMITTING, SCHEDULE
  3. Each category is batched and sent to Claude CLI with the appropriate specialist prompt
  4. Claude generates solution-oriented drafts (NOT generic "just checking in" garbage)
  5. Drafts are parsed and combined into a professional PDF

Specialist routing:
  CONSTRUCTION → construction_manager brain
  ENGINEERING  → construction_manager brain (interface between eng and field)
  PERMITTING   → construction_manager brain (jurisdictional navigation)
  PROCUREMENT  → cost_analyst brain (supply chain, alternate sourcing)
  SCHEDULE     → scheduling_expert brain (CPM, float recovery, crashing)

Usage:
  # Pull fresh data and generate report
  node /opt/goliath/scripts/pull_constraints_direct.mjs > /tmp/all_constraints.json
  python3 generate_followup_pdf.py /tmp/all_constraints.json [output.pdf]

  # Or with --use-templates to skip Claude calls (fast but dumb, for testing)
  python3 generate_followup_pdf.py /tmp/all_constraints.json --use-templates
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable,
    NextPageTemplate, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

CT = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------------
# Constraint categorization
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = {
    "CONSTRUCTION": [
        "pile", "piling", "tracker", "racking", "module", "panel",
        "grading", "civil", "fencing", "erosion", "swppp", "substation",
        "trenching", "cable", "wire", "conduit", "install", "crew",
        "labor", "mobilization", "site prep", "foundation", "concrete",
        "commissioning", "energization", "testing", "punch list",
        "construction", "field", "site", "build", "work",
    ],
    "PROCUREMENT": [
        "procurement", "purchase", "order", "delivery", "shipment",
        "vendor", "supplier", "material", "equipment", "lead time",
        "invoice", "payment", "cost", "budget", "price", "tariff",
        "po ", "purchase order", "rfp", "rfq", "bid", "quote",
        "supply chain", "warehouse", "inventory", "shortage",
        "backorder", "expedite", "freight", "logistics",
    ],
    "ENGINEERING": [
        "engineering", "design", "drawing", "ifc", "ifd",
        "redline", "as-built", "spec", "specification",
        "calculation", "study", "review", "stamp", "seal",
        "electrical", "structural", "geotechnical", "geotech",
        "single line", "one line", "layout", "plan set",
    ],
    "PERMITTING": [
        "permit", "permitting", "approval", "authority",
        "jurisdiction", "ahj", "inspection", "compliance",
        "environmental", "wetland", "easement", "right of way",
        "interconnection", "utility", "county", "state",
        "zoning", "variance",
    ],
    "SCHEDULE": [
        "schedule", "critical path", "float", "delay", "slip",
        "milestone", "deadline", "substantial completion",
        "mechanical completion", "cod", "ntp", "notice to proceed",
        "baseline", "lookahead", "recovery", "acceleration",
    ],
}

CATEGORY_EMOJI = {
    "CONSTRUCTION": "🏗️",
    "PROCUREMENT": "📦",
    "ENGINEERING": "📐",
    "PERMITTING": "📋",
    "SCHEDULE": "📅",
}

# ---------------------------------------------------------------------------
# Specialist prompts — the BRAINS behind each category
# ---------------------------------------------------------------------------

SPECIALIST_PROMPTS = {
    "CONSTRUCTION": """\
You are a senior construction manager who has been on 100+ utility-scale solar jobsites. \
You know crew productivity, sequencing, site logistics, and what actually works in the field.

Generate a solution-oriented follow-up email draft for EACH constraint below. Each draft MUST:
- Start with "Hi <owner_first_name>,"
- End with "Thanks, Aaron"
- Be 4-6 sentences, plain text only (no HTML/markdown)
- Propose SPECIFIC, ACTIONABLE solutions based on the constraint type:
  * Crew/labor issues → suggest crew size adjustments, shift changes, sub mobilization, overtime
  * Site conditions → suggest mitigation (dewatering, soil stabilization, matting, grading adjustments)
  * Sequencing → suggest re-sequencing options, parallel activities, out-of-sequence work permits
  * Equipment → suggest alternatives, rental options, shared resources between projects
- Reference the specific project and constraint details to show you READ and UNDERSTAND it
- NEVER write generic "just checking in" or "any update?" messages. Every draft must have a REAL suggestion.

For each constraint, output the draft between markers exactly like this:
===DRAFT_START id=<constraint_id>===
<the email draft text>
===DRAFT_END===
""",

    "ENGINEERING": """\
You are a senior construction manager who manages the interface between engineering and construction \
on utility-scale solar projects. You understand IFC packages, design review cycles, and how \
engineering delays cascade into field work.

Generate a solution-oriented follow-up email draft for EACH constraint below. Each draft MUST:
- Start with "Hi <owner_first_name>,"
- End with "Thanks, Aaron"
- Be 4-6 sentences, plain text only
- Propose SPECIFIC solutions:
  * Drawing reviews → suggest parallel review tracks, phased IFC releases, redline-and-proceed
  * Design issues → ask about alternatives that avoid redesign entirely
  * Calculations/studies → suggest whether field data could accelerate the deliverable
  * Specs → suggest design-build approach or performance specs vs. prescriptive
- NEVER write generic "just checking in" messages.

For each constraint, output the draft between markers exactly like this:
===DRAFT_START id=<constraint_id>===
<the email draft text>
===DRAFT_END===
""",

    "PERMITTING": """\
You are a senior construction manager who navigates jurisdictional approvals, AHJ requirements, \
environmental compliance, and utility interconnection for solar projects.

Generate a solution-oriented follow-up email draft for EACH constraint below. Each draft MUST:
- Start with "Hi <owner_first_name>,"
- End with "Thanks, Aaron"
- Be 4-6 sentences, plain text only
- Propose SPECIFIC solutions:
  * AHJ delays → suggest pre-inspection conferences, political escalation channels, phased permits
  * Environmental → suggest phased clearing, seasonal work windows, mitigation credits, SWPPP mods
  * Interconnection → suggest utility liaison meetings, independent engineer review, parallel tracks
  * Easements/ROW → suggest legal review acceleration, temporary access agreements, good neighbor letters
- NEVER write generic messages.

For each constraint, output the draft between markers exactly like this:
===DRAFT_START id=<constraint_id>===
<the email draft text>
===DRAFT_END===
""",

    "PROCUREMENT": """\
You are a cost/procurement analyst who knows solar supply chains inside and out. You understand \
module lead times, tracker procurement, BOS materials, tariff impacts, and vendor management.

Generate a solution-oriented follow-up email draft for EACH constraint below. Each draft MUST:
- Start with "Hi <owner_first_name>,"
- End with "Thanks, Aaron"
- Be 4-6 sentences, plain text only
- Propose SPECIFIC solutions:
  * Delivery delays → suggest alternate sourcing, partial shipments, schedule re-sequence to work around
  * Cost issues → suggest value engineering, bulk discounts, phased ordering, or alternate specs
  * Lead time → calculate if expediting is worth the cost vs. schedule impact, suggest split POs
  * Vendor issues → suggest backup vendors, consolidated POs for leverage, early payment discounts
- NEVER write generic "just checking in" messages.

For each constraint, output the draft between markers exactly like this:
===DRAFT_START id=<constraint_id>===
<the email draft text>
===DRAFT_END===
""",

    "SCHEDULE": """\
You are a CPM scheduling expert who lives and breathes critical path methodology, float analysis, \
and schedule recovery on utility-scale solar projects. You think in P6 logic ties and resource loading.

Generate a solution-oriented follow-up email draft for EACH constraint below. Each draft MUST:
- Start with "Hi <owner_first_name>,"
- End with "Thanks, Aaron"
- Be 4-6 sentences, plain text only
- Propose SPECIFIC solutions:
  * Float issues → quantify the impact and suggest where to recover days (crashing, fast-tracking)
  * Milestone slips → identify which predecessor activities are likely driving the delay
  * Resource-driven → suggest shift work, additional crews, equipment changes, or weekend work
  * Logic tie issues → suggest re-sequencing, removing unnecessary dependencies, lead/lag adjustments
- NEVER write generic messages.

For each constraint, output the draft between markers exactly like this:
===DRAFT_START id=<constraint_id>===
<the email draft text>
===DRAFT_END===
""",
}

# Max constraints per Claude batch (to avoid token limits)
BATCH_SIZE = 35


def categorize(constraint):
    """Determine category based on description + discipline."""
    existing = (constraint.get("discipline") or constraint.get("category") or "").upper()
    if existing in CATEGORY_KEYWORDS:
        return existing

    desc = (
        (constraint.get("description", "") or "") + " " +
        (constraint.get("notes", "") or "")
    ).lower()

    best = "CONSTRUCTION"
    best_score = 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc)
        if score > best_score:
            best_score = score
            best = cat
    return best


def generate_template_draft(constraint, category):
    """FALLBACK ONLY — simple template draft when Claude is unavailable."""
    owner = constraint.get("owner", "Unassigned")
    owner_first = owner.split()[0] if owner and owner != "Unassigned" else "Team"
    project = constraint.get("project", "Unknown")
    desc = constraint.get("description", "No description")
    days_open = constraint.get("days_open")
    need_by = constraint.get("need_by_date")
    priority = constraint.get("priority", "MEDIUM")

    days_str = f"{days_open} days" if days_open else "unknown duration"
    need_by_str = f" (need-by: {need_by})" if need_by else ""

    if priority == "HIGH" or (days_open and days_open > 30):
        draft = (
            f"Hi {owner_first},\n\n"
            f"Following up on the {project} constraint: \"{desc}\"\n\n"
            f"This has been open {days_str}{need_by_str} and is flagged {priority} priority. "
            f"Can you provide a status update or let me know if there's anything blocking resolution? "
            f"Happy to jump on a call if that's easier.\n\n"
            f"If there's a different path we should consider, I'm open to alternatives — "
            f"just want to make sure we're moving this forward.\n\n"
            f"Thanks,\nAaron"
        )
    else:
        draft = (
            f"Hi {owner_first},\n\n"
            f"Quick check-in on the {project} constraint: \"{desc}\"\n\n"
            f"This has been open {days_str}{need_by_str}. "
            f"Any updates on your end? Let me know if there's anything I can help with "
            f"to get this resolved.\n\n"
            f"Thanks,\nAaron"
        )

    return draft


def format_constraint_block(idx, constraint):
    """Format a single constraint for inclusion in the batch prompt."""
    cid = constraint.get("id", f"unknown_{idx}")
    project = constraint.get("project", "Unknown")
    desc = constraint.get("description", "No description")
    owner = constraint.get("owner", "Unassigned")
    priority = constraint.get("priority", "MEDIUM")
    days_open = constraint.get("days_open", "?")
    need_by = constraint.get("need_by_date", "Not set")
    notes = constraint.get("notes", "")

    block = (
        f"[{idx + 1}] ID: {cid}\n"
        f"    Project: {project}\n"
        f"    Priority: {priority}\n"
        f"    Days open: {days_open}\n"
        f"    Need-by date: {need_by}\n"
        f"    Description: {desc}\n"
        f"    Owner: {owner}\n"
    )
    if notes and notes.strip():
        block += f"    Latest notes: {notes[:300]}\n"

    return block


def call_claude_batch(specialist_prompt, constraints, category):
    """Call Claude CLI with a batch of constraints and the specialist prompt.

    Returns a dict mapping constraint_id → draft text.
    """
    # Build the full prompt
    constraint_blocks = []
    for idx, c in enumerate(constraints):
        constraint_blocks.append(format_constraint_block(idx, c))

    full_prompt = (
        f"{specialist_prompt}\n"
        f"Here are {len(constraints)} constraints that need follow-up drafts:\n\n"
        f"{''.join(constraint_blocks)}\n\n"
        f"Generate a unique, solution-oriented draft for EACH constraint. "
        f"Remember: specific suggestions, NOT generic check-ins."
    )

    # Write prompt to temp file (avoids shell escaping issues with long prompts)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(full_prompt)
        prompt_file = f.name

    try:
        print(f"  Calling Claude for {len(constraints)} {category} constraints...")
        start = time.monotonic()

        # Must unset CLAUDECODE env var to avoid "nested session" error
        # when running from within a Claude Code session
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)

        result = subprocess.run(
            ["claude", "--print", "--max-turns", "1"],
            stdin=open(prompt_file),
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout per batch
            env=env,
        )

        duration = time.monotonic() - start
        print(f"  Claude responded in {duration:.1f}s")

        if result.returncode != 0:
            print(f"  WARNING: Claude returned non-zero exit code: {result.returncode}")
            print(f"  stderr: {result.stderr[:500]}")
            return {}

        output = result.stdout

        # Parse the drafts from the output
        drafts = parse_draft_output(output, constraints)
        print(f"  Parsed {len(drafts)} drafts from {len(constraints)} constraints")
        return drafts

    except subprocess.TimeoutExpired:
        print(f"  ERROR: Claude timed out after 300s for {category} batch")
        return {}
    except Exception as e:
        print(f"  ERROR: Claude call failed: {e}")
        return {}
    finally:
        os.unlink(prompt_file)


def parse_draft_output(output, constraints):
    """Parse Claude's output to extract individual drafts.

    Expects format:
    ===DRAFT_START id=<constraint_id>===
    <draft text>
    ===DRAFT_END===
    """
    drafts = {}

    # Primary parsing: look for structured markers
    pattern = r'===DRAFT_START\s+id=([^=]+?)===\s*\n(.*?)\n===DRAFT_END==='
    matches = re.findall(pattern, output, re.DOTALL)

    for cid, draft_text in matches:
        cid = cid.strip()
        draft_text = draft_text.strip()
        if draft_text:
            drafts[cid] = draft_text

    # If structured parsing got less than half, try fallback heuristic parsing
    if len(drafts) < len(constraints) // 2:
        print(f"  Structured parsing found {len(drafts)}/{len(constraints)} — trying fallback...")
        fallback_drafts = parse_draft_fallback(output, constraints)
        # Merge, preferring structured where available
        for cid, draft in fallback_drafts.items():
            if cid not in drafts:
                drafts[cid] = draft

    return drafts


def parse_draft_fallback(output, constraints):
    """Fallback parser: try to split output by constraint ID mentions or [N] markers."""
    drafts = {}

    # Try splitting by [N] markers that match constraint indices
    for idx, c in enumerate(constraints):
        cid = c.get("id", f"unknown_{idx}")
        # Look for sections starting with [idx+1] or the constraint ID
        pattern = rf'\[{idx + 1}\].*?(?=\[{idx + 2}\]|\Z)'
        match = re.search(pattern, output, re.DOTALL)
        if match:
            text = match.group(0).strip()
            # Try to extract just the email part (starts with "Hi")
            hi_match = re.search(r'(Hi\s+\w+.*?Thanks,\s*Aaron)', text, re.DOTALL)
            if hi_match:
                drafts[cid] = hi_match.group(1).strip()

    return drafts


def generate_specialist_drafts(categorized_constraints):
    """Generate specialist drafts for all constraints using batched Claude calls.

    Groups constraints by category, sends each group to Claude with the
    appropriate specialist prompt, and returns a dict of constraint_id → draft.
    """
    # Group by category
    by_category = {}
    for item in categorized_constraints:
        cat = item["category"]
        by_category.setdefault(cat, []).append(item["constraint"])

    all_drafts = {}

    # Process each category — run specialist batches in parallel using threads
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}

        for category, constraints in by_category.items():
            prompt = SPECIALIST_PROMPTS.get(category, SPECIALIST_PROMPTS["CONSTRUCTION"])

            # Split into sub-batches if category has too many constraints
            for batch_start in range(0, len(constraints), BATCH_SIZE):
                batch = constraints[batch_start : batch_start + BATCH_SIZE]
                batch_label = f"{category}[{batch_start}:{batch_start + len(batch)}]"

                future = executor.submit(call_claude_batch, prompt, batch, batch_label)
                futures[future] = (category, batch)

        # Collect results
        for future in as_completed(futures):
            category, batch = futures[future]
            try:
                batch_drafts = future.result()
                all_drafts.update(batch_drafts)
            except Exception as e:
                print(f"  ERROR: Batch failed for {category}: {e}")

    return all_drafts


def build_pdf(constraints, output_path):
    """Build the professional follow-up PDF."""

    # Brand colors
    NAVY = colors.HexColor("#1B365D")
    RED = colors.HexColor("#CC0000")
    AMBER = colors.HexColor("#CC8800")
    GREEN = colors.HexColor("#228B22")
    LIGHT_GREY = colors.HexColor("#F5F5F5")
    MID_GREY = colors.HexColor("#E0E0E0")
    WHITE = colors.white
    BLACK = colors.black
    ACCENT_BLUE = colors.HexColor("#3A7BD5")

    PRIORITY_COLORS = {"HIGH": RED, "MEDIUM": AMBER, "LOW": GREEN}

    _base = getSampleStyleSheet()

    def _s(name, **kw):
        return ParagraphStyle(name, parent=_base["Normal"], **kw)

    STYLE_H1 = _s("H1", fontName="Helvetica-Bold", fontSize=14,
                   textColor=NAVY, leading=20, spaceBefore=16, spaceAfter=6)
    STYLE_H2 = _s("H2", fontName="Helvetica-Bold", fontSize=11,
                   textColor=NAVY, leading=16, spaceBefore=10, spaceAfter=4)
    STYLE_BODY = _s("Body", fontName="Helvetica", fontSize=9,
                     textColor=BLACK, leading=12, spaceAfter=3)
    STYLE_BODY_BOLD = _s("BodyBold", fontName="Helvetica-Bold", fontSize=9,
                          textColor=BLACK, leading=12, spaceAfter=3)
    STYLE_DRAFT = _s("Draft", fontName="Courier", fontSize=8.5,
                      textColor=colors.HexColor("#333333"), leading=11,
                      leftIndent=12, rightIndent=12, spaceBefore=4, spaceAfter=4,
                      backColor=colors.HexColor("#F8F9FA"),
                      borderWidth=0.5, borderColor=MID_GREY, borderPadding=6)
    STYLE_SMALL = _s("Small", fontName="Helvetica", fontSize=7.5,
                      textColor=colors.HexColor("#666666"), leading=10)
    STYLE_TABLE_HEADER = _s("TH", fontName="Helvetica-Bold", fontSize=8,
                             textColor=WHITE, leading=10)
    STYLE_TABLE_CELL = _s("TC", fontName="Helvetica", fontSize=8,
                           textColor=BLACK, leading=10)

    today = datetime.now(CT)
    date_str = today.strftime("%B %d, %Y")
    day_label = today.strftime("%A")

    # Organize by project
    by_project = {}
    for item in constraints:
        proj = item["constraint"]["project"]
        by_project.setdefault(proj, []).append(item)

    # Sort within each project: HIGH > MEDIUM > LOW, then by days open desc
    priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for proj in by_project:
        by_project[proj].sort(
            key=lambda x: (
                priority_order.get(x["constraint"].get("priority", "LOW"), 3),
                -(x["constraint"].get("days_open") or 0),
            )
        )

    # Count stats
    total = len(constraints)
    high_count = sum(1 for c in constraints if c["constraint"].get("priority") == "HIGH")
    medium_count = sum(1 for c in constraints if c["constraint"].get("priority") == "MEDIUM")
    low_count = sum(1 for c in constraints if c["constraint"].get("priority") == "LOW")
    specialist_count = sum(1 for c in constraints if c.get("specialist_generated"))

    w, h = letter

    report_title = "Follow-Up Report"

    def _header_footer(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - 45, w, 45, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(0.75 * inch, h - 30, f"GOLIATH {report_title}")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#B0C4DE"))
        canvas.drawRightString(w - 0.75 * inch, h - 30, date_str)
        canvas.setStrokeColor(MID_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
        canvas.setFillColor(colors.HexColor("#999999"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(0.75 * inch, 0.35 * inch,
                          "Generated by GOLIATH — Copy-paste drafts into emails. No auto-send.")
        canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    def _first_page(canvas, doc):
        canvas.saveState()
        header_h = 90
        canvas.setFillColor(NAVY)
        canvas.rect(0, h - header_h, w, header_h, stroke=0, fill=1)
        canvas.setFillColor(ACCENT_BLUE)
        canvas.rect(0, h - header_h, w, 3, stroke=0, fill=1)
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica-Bold", 22)
        canvas.drawString(0.75 * inch, h - 40, report_title)
        canvas.setFont("Helvetica", 11)
        canvas.setFillColor(colors.HexColor("#B0C4DE"))
        subtitle = (
            f"{date_str}  |  {total} constraints  |  "
            f"{len(by_project)} projects"
        )
        if specialist_count:
            subtitle += f"  |  {specialist_count} specialist drafts"
        canvas.drawString(0.75 * inch, h - 58, subtitle)
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(colors.HexColor("#8FAFD0"))
        canvas.drawString(0.75 * inch, h - 74,
                          "Solution-oriented follow-up drafts — organized by project and priority")
        canvas.setStrokeColor(MID_GREY)
        canvas.setLineWidth(0.5)
        canvas.line(0.75 * inch, 0.5 * inch, w - 0.75 * inch, 0.5 * inch)
        canvas.setFillColor(colors.HexColor("#999999"))
        canvas.setFont("Helvetica", 6.5)
        canvas.drawString(0.75 * inch, 0.35 * inch,
                          "Generated by GOLIATH — Copy-paste drafts into emails. No auto-send.")
        canvas.drawRightString(w - 0.75 * inch, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()

    # Build document
    first_frame = Frame(
        0.75 * inch, 0.75 * inch,
        w - 1.5 * inch, h - 1.75 * inch - 90,
        id="first_frame",
    )
    later_frame = Frame(
        0.75 * inch, 0.75 * inch,
        w - 1.5 * inch, h - 1.75 * inch,
        id="later_frame",
    )

    doc = BaseDocTemplate(
        str(output_path),
        pagesize=letter,
        title=f"GOLIATH {report_title}",
        author="GOLIATH Construction Operations",
    )
    doc.addPageTemplates([
        PageTemplate(id="first", frames=[first_frame], onPage=_first_page),
        PageTemplate(id="later", frames=[later_frame], onPage=_header_footer),
    ])

    elements = []

    # Executive summary table
    elements.append(Paragraph("Executive Summary", STYLE_H1))

    summary_data = [
        [
            Paragraph("Total", STYLE_TABLE_HEADER),
            Paragraph("HIGH", STYLE_TABLE_HEADER),
            Paragraph("MEDIUM", STYLE_TABLE_HEADER),
            Paragraph("LOW", STYLE_TABLE_HEADER),
            Paragraph("Projects", STYLE_TABLE_HEADER),
            Paragraph("AI Drafts", STYLE_TABLE_HEADER),
        ],
        [
            Paragraph(str(total), STYLE_TABLE_CELL),
            Paragraph(str(high_count), STYLE_TABLE_CELL),
            Paragraph(str(medium_count), STYLE_TABLE_CELL),
            Paragraph(str(low_count), STYLE_TABLE_CELL),
            Paragraph(str(len(by_project)), STYLE_TABLE_CELL),
            Paragraph(str(specialist_count), STYLE_TABLE_CELL),
        ],
    ]
    summary_table = Table(summary_data, colWidths=[1.0 * inch] * 6)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT_GREY),
        ("GRID", (0, 0), (-1, -1), 0.5, MID_GREY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    # Switch to later pages template after first page content
    elements.append(NextPageTemplate("later"))

    # Per-project sections
    for proj_name in sorted(by_project.keys()):
        items = by_project[proj_name]
        proj_high = sum(1 for i in items if i["constraint"].get("priority") == "HIGH")
        proj_med = sum(1 for i in items if i["constraint"].get("priority") == "MEDIUM")
        proj_low = sum(1 for i in items if i["constraint"].get("priority") == "LOW")

        elements.append(HRFlowable(width="100%", thickness=1, color=NAVY, spaceBefore=8, spaceAfter=8))
        elements.append(Paragraph(
            f"{proj_name} — {len(items)} constraints "
            f"(<font color='#CC0000'>{proj_high}H</font> / "
            f"<font color='#CC8800'>{proj_med}M</font> / "
            f"<font color='#228B22'>{proj_low}L</font>)",
            STYLE_H1,
        ))

        for item in items:
            c = item["constraint"]
            priority = c.get("priority", "MEDIUM")
            pcolor = PRIORITY_COLORS.get(priority, AMBER)
            cat = item.get("category", "CONSTRUCTION")
            cat_emoji = CATEGORY_EMOJI.get(cat, "🔧")
            is_specialist = item.get("specialist_generated", False)

            # Constraint header
            desc_safe = (c.get("description") or "No description").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            specialist_tag = ' <font color="#3A7BD5">[AI]</font>' if is_specialist else ' <font color="#999999">[template]</font>'
            elements.append(Paragraph(
                f'<font color="{pcolor.hexval()}">[{priority}]</font> '
                f'{cat_emoji} {desc_safe}{specialist_tag}',
                STYLE_BODY_BOLD,
            ))

            # Metadata line
            owner = c.get("owner", "Unassigned")
            days = c.get("days_open")
            need_by = c.get("need_by_date")
            meta_parts = [f"<b>Owner:</b> {owner}"]
            if days is not None:
                meta_parts.append(f"<b>Days open:</b> {days}")
            if need_by:
                meta_parts.append(f"<b>Need-by:</b> {need_by}")
            meta_parts.append(f"<b>Category:</b> {cat}")
            elements.append(Paragraph("  |  ".join(meta_parts), STYLE_SMALL))

            # Notes if any
            notes = c.get("notes", "")
            if notes and notes.strip():
                notes_safe = notes[:300].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                elements.append(Paragraph(f"<i>Latest note: {notes_safe}</i>", STYLE_SMALL))

            # Draft
            draft = item.get("draft", "")
            if draft:
                draft_safe = draft.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
                elements.append(Paragraph(draft_safe, STYLE_DRAFT))

            elements.append(Spacer(1, 8))

    # Build
    doc.build(elements)
    print(f"PDF generated: {output_path}")
    return True


def main():
    # Parse args
    use_templates = "--use-templates" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    input_file = args[0] if args else "/tmp/all_constraints.json"
    output_file = args[1] if len(args) > 1 else None

    with open(input_file) as f:
        raw_constraints = json.load(f)

    print(f"Processing {len(raw_constraints)} constraints...")
    total_start = time.monotonic()

    # Categorize all constraints
    categorized = []
    for c in raw_constraints:
        cat = categorize(c)
        categorized.append({
            "constraint": c,
            "category": cat,
            "draft": None,
            "specialist_generated": False,
        })

    if use_templates:
        # Fast mode — use dumb templates (for testing only)
        print("Using template drafts (--use-templates mode)...")
        for item in categorized:
            item["draft"] = generate_template_draft(item["constraint"], item["category"])
    else:
        # Smart mode — use specialist brains via Claude CLI
        print("Generating specialist drafts via Claude CLI...")
        specialist_drafts = generate_specialist_drafts(categorized)
        print(f"Got {len(specialist_drafts)} specialist drafts total")

        # Assign drafts to items
        for item in categorized:
            cid = item["constraint"].get("id", "")
            if cid in specialist_drafts:
                item["draft"] = specialist_drafts[cid]
                item["specialist_generated"] = True
            else:
                # Fallback to template for any that the specialist missed
                item["draft"] = generate_template_draft(item["constraint"], item["category"])
                print(f"  Fallback template for: {item['constraint'].get('description', '?')[:60]}")

    # Generate PDF
    today = datetime.now(CT)
    if output_file is None:
        output_file = f"/opt/goliath/reports/{today.strftime('%Y-%m-%d')}-followup-report.pdf"

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    build_pdf(categorized, output_path)

    duration = time.monotonic() - total_start
    specialist_count = sum(1 for c in categorized if c.get("specialist_generated"))
    template_count = len(categorized) - specialist_count
    print(f"\nDone in {duration:.1f}s — {specialist_count} specialist drafts, {template_count} template fallbacks")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
