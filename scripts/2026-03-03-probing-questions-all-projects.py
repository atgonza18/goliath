#!/usr/bin/env python3
"""
Generate Consolidated Probing Questions PDF for All Projects — March 3, 2026
Meeting prep for: Tehuacana, Duffy BESS, Pecan Prairie, Duff Solar, Scioto Ridge, Mayes, Delta Bobcat
Live data pulled from ConstraintsPro.
"""

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.lib.colors import HexColor
import json
import os
from datetime import datetime, timedelta

TODAY = datetime(2026, 3, 3)
OUTPUT_DIR = "/opt/goliath/reports"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "2026-03-03-probing-questions-all-projects.pdf")

# Color definitions
NAVY = HexColor("#1B365D")
RED = HexColor("#C0392B")
RED_LIGHT = HexColor("#FADBD8")
ORANGE = HexColor("#E67E22")
ORANGE_LIGHT = HexColor("#FDEBD0")
GREEN = HexColor("#27AE60")
GREEN_LIGHT = HexColor("#D5F5E3")
DARK_GRAY = HexColor("#2C3E50")
MED_GRAY = HexColor("#7F8C8D")
LIGHT_GRAY = HexColor("#ECF0F1")
WHITE = colors.white
BLACK = colors.black
URGENT_RED = HexColor("#E74C3C")
HIGH_BG = HexColor("#FDEDEC")
MEDIUM_BG = HexColor("#FEF9E7")
LOW_BG = HexColor("#EBF5FB")


def build_styles():
    styles = getSampleStyleSheet()
    custom_styles = {
        'DocTitle': ParagraphStyle(
            'DocTitle', fontName='Helvetica-Bold', fontSize=22, leading=28,
            alignment=TA_CENTER, textColor=NAVY, spaceAfter=6,
        ),
        'DocSubtitle': ParagraphStyle(
            'DocSubtitle', fontName='Helvetica', fontSize=12, leading=16,
            alignment=TA_CENTER, textColor=MED_GRAY, spaceAfter=4,
        ),
        'DocDate': ParagraphStyle(
            'DocDate', fontName='Helvetica-Oblique', fontSize=11, leading=14,
            alignment=TA_CENTER, textColor=MED_GRAY, spaceAfter=20,
        ),
        'ProjectHeader': ParagraphStyle(
            'ProjectHeader', fontName='Helvetica-Bold', fontSize=16, leading=22,
            textColor=WHITE, spaceAfter=0, spaceBefore=0, leftIndent=8,
        ),
        'MeetingTime': ParagraphStyle(
            'MeetingTime', fontName='Helvetica-Bold', fontSize=10, leading=14,
            textColor=NAVY, spaceBefore=2, spaceAfter=4,
        ),
        'ConstraintHeader': ParagraphStyle(
            'ConstraintHeader', fontName='Helvetica-Bold', fontSize=10, leading=14,
            textColor=DARK_GRAY, spaceBefore=8, spaceAfter=2, leftIndent=4,
        ),
        'ConstraintDesc': ParagraphStyle(
            'ConstraintDesc', fontName='Helvetica', fontSize=9.5, leading=13,
            textColor=DARK_GRAY, leftIndent=12, spaceAfter=1,
        ),
        'ConstraintMeta': ParagraphStyle(
            'ConstraintMeta', fontName='Helvetica-Oblique', fontSize=8.5, leading=11,
            textColor=MED_GRAY, leftIndent=12, spaceAfter=2,
        ),
        'QuestionText': ParagraphStyle(
            'QuestionText', fontName='Helvetica', fontSize=9.5, leading=13,
            textColor=DARK_GRAY, leftIndent=24, spaceAfter=3,
        ),
        'UrgentQuestion': ParagraphStyle(
            'UrgentQuestion', fontName='Helvetica-Bold', fontSize=9.5, leading=13,
            textColor=URGENT_RED, leftIndent=24, spaceAfter=3,
        ),
        'ContextNote': ParagraphStyle(
            'ContextNote', fontName='Helvetica-Oblique', fontSize=8.5, leading=11,
            textColor=MED_GRAY, leftIndent=34, spaceAfter=4,
        ),
        'CategoryHeader': ParagraphStyle(
            'CategoryHeader', fontName='Helvetica-Bold', fontSize=11, leading=15,
            textColor=DARK_GRAY, spaceBefore=10, spaceAfter=4, leftIndent=6,
        ),
        'IntroText': ParagraphStyle(
            'IntroText', fontName='Helvetica', fontSize=10, leading=14,
            textColor=DARK_GRAY, spaceAfter=6,
        ),
        'Footer': ParagraphStyle(
            'Footer', fontName='Helvetica-Oblique', fontSize=8, leading=10,
            alignment=TA_CENTER, textColor=MED_GRAY,
        ),
        'NoConstraints': ParagraphStyle(
            'NoConstraints', fontName='Helvetica-Oblique', fontSize=10, leading=14,
            textColor=MED_GRAY, leftIndent=12, spaceAfter=6,
        ),
        'SummaryText': ParagraphStyle(
            'SummaryText', fontName='Helvetica', fontSize=9, leading=12,
            textColor=DARK_GRAY, leftIndent=12, spaceAfter=3,
        ),
    }
    for name, style in custom_styles.items():
        styles.add(style)
    return styles


def get_last_note(notes_str):
    if not notes_str:
        return ""
    lines = [l.strip() for l in notes_str.split('\n') if l.strip()]
    return lines[-1] if lines else ""


def days_overdue(due_date_ms):
    if not due_date_ms:
        return 0
    due = datetime.fromtimestamp(due_date_ms / 1000)
    delta = (TODAY - due).days
    return max(0, delta)


def generate_questions(constraint):
    """Generate smart probing questions based on constraint data."""
    questions = []
    desc = constraint.get('description', '')
    status = constraint.get('status', '')
    priority = constraint.get('priority', '')
    discipline = constraint.get('discipline', '')
    due_ms = constraint.get('dueDate', None)
    notes = constraint.get('notes', '')
    last_note = get_last_note(notes)
    overdue = days_overdue(due_ms)

    due_str = ""
    if due_ms:
        due_str = datetime.fromtimestamp(due_ms / 1000).strftime('%b %d')

    # --- OVERDUE QUESTIONS ---
    if overdue > 14:
        questions.append({
            "text": f"This was due {due_str} — <b>{overdue} days ago</b>. What is specifically blocking resolution, and who owns the next action?",
            "urgent": True,
            "context": f"Overdue by {overdue} days. Needs immediate escalation path."
        })
    elif overdue > 7:
        questions.append({
            "text": f"Due date was {due_str} ({overdue} days ago). What's changed since then, and what's the revised target date?",
            "urgent": True,
            "context": f"Over a week past due — needs a firm new commitment."
        })
    elif overdue > 0:
        questions.append({
            "text": f"This was due {due_str} ({overdue} days ago). Has it been resolved, or do we need a new target?",
            "urgent": priority == 'high',
            "context": f"Recently past due — confirm current status."
        })

    # --- PRIORITY-BASED QUESTIONS ---
    if priority == 'high' and status != 'resolved':
        if not any(q.get('urgent') for q in questions):
            questions.append({
                "text": f"This is flagged <b>HIGH priority</b>. What's the specific path to resolution and who's driving it?",
                "urgent": True,
                "context": "High priority items need clear ownership and timeline."
            })

    # --- STATUS-BASED QUESTIONS ---
    if status == 'in_progress':
        if last_note and '3/2' in last_note:
            # Recent update from yesterday
            questions.append({
                "text": f"Yesterday's update: \"{last_note[:120]}...\" — Has anything changed overnight? What's the next milestone?",
                "urgent": False,
            })
        elif last_note:
            # Extract key info from last note
            questions.append({
                "text": f"Last update: \"{last_note[:120]}\" — What progress has been made since? Is the timeline holding?",
                "urgent": False,
            })
    elif status == 'open':
        if not last_note or ('no update' in last_note.lower()):
            questions.append({
                "text": f"There's been no meaningful update on this. Who is the single point of accountability, and when can we expect movement?",
                "urgent": priority in ['high', 'medium'],
                "context": "Stalled constraint — needs someone to own it."
            })

    # --- DISCIPLINE-SPECIFIC QUESTIONS ---
    desc_lower = desc.lower()
    note_lower = last_note.lower() if last_note else ""

    if discipline in ['Procurement', 'Logistics']:
        if 'delivery' in desc_lower or 'delivery' in note_lower:
            questions.append({
                "text": "Do we have a confirmed delivery date with tracking? If not, who is the vendor contact we need to chase?",
                "urgent": priority == 'high',
            })
        if 'po' in desc_lower.split() or 'contract' in desc_lower or 'subcontract' in note_lower:
            questions.append({
                "text": "What's the execution status? Is this stuck in legal/procurement review, or waiting on a counterparty?",
                "urgent": False,
            })

    if discipline in ['Piles', 'Racking', 'Modules', 'AG Electrical', 'Civil', 'Civil Engineering']:
        if 'production' in desc_lower or 'schedule' in desc_lower or 'impacting' in desc_lower:
            questions.append({
                "text": "What's the daily production rate vs. what's required? Is the current plan realistic to recover?",
                "urgent": priority == 'high',
            })
        if 'remediation' in desc_lower or 'remid' in desc_lower or 'remed' in note_lower:
            questions.append({
                "text": "What's the scope of remediation — how many units affected, what's the crew plan, and when does it complete?",
                "urgent": False,
            })

    if discipline in ['Engineering', 'Civil Engineering']:
        if 'permit' in desc_lower or 'permit' in note_lower:
            questions.append({
                "text": "What's the permitting authority's timeline? Have we escalated to expedite? Is there a workaround if permits are delayed further?",
                "urgent": overdue > 0,
            })
        if 'ppp' in desc_lower or 'surface file' in note_lower or 'ppp' in note_lower:
            questions.append({
                "text": "How many PPPs are outstanding vs. total needed? What's the EOR turnaround time running?",
                "urgent": False,
            })

    if 'pd-10' in desc_lower or 'pd10' in desc_lower or 'pd-10' in note_lower or 'pd10' in note_lower:
        questions.append({
            "text": "How many PD-10s are currently operational vs. down? What parts are needed and when do they arrive?",
            "urgent": priority == 'high',
        })

    if 'weather' in desc_lower or 'ground condition' in desc_lower or 'rain' in note_lower:
        questions.append({
            "text": "What's the weather forecast for the next 7-10 days? Do we have a wet-weather contingency plan?",
            "urgent": False,
        })

    if 'co ' in desc_lower or 'change order' in desc_lower or ' co ' in note_lower:
        questions.append({
            "text": "Where is the CO in the approval process? Who has it, and when is the decision expected?",
            "urgent": priority == 'high',
        })

    # Deduplicate and limit to 3 questions max
    seen_texts = set()
    unique_questions = []
    for q in questions:
        key = q['text'][:60]
        if key not in seen_texts:
            seen_texts.add(key)
            unique_questions.append(q)

    # Ensure at least 1 question
    if not unique_questions:
        unique_questions.append({
            "text": f"What's the current status and expected resolution date? Who owns the next action?",
            "urgent": False,
        })

    return unique_questions[:3]


def make_project_header(name, meeting_time, constraint_count, styles):
    """Create a colored header bar for a project section."""
    if constraint_count == 0:
        bg = MED_GRAY
    elif constraint_count > 10:
        bg = RED
    elif constraint_count > 5:
        bg = ORANGE
    else:
        bg = GREEN

    header_text = f"{name}"
    header_para = Paragraph(header_text, styles['ProjectHeader'])

    # Stats line
    stats_style = ParagraphStyle(
        'StatsLine', fontName='Helvetica', fontSize=9, leading=12,
        textColor=HexColor("#FFFFFFCC"),
    )

    header_table = Table(
        [[header_para]],
        colWidths=[7.0 * inch],
        rowHeights=[34],
    )
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    return header_table


def build_project_section(project_name, meeting_time, constraints, styles):
    """Build a full project section with constraints and probing questions."""
    elements = []

    # Filter to open/in_progress only
    active = [c for c in constraints if c.get('status') in ('open', 'in_progress')]

    # Sort: high priority first, then by overdue days
    def sort_key(c):
        prio_order = {'high': 0, 'medium': 1, 'low': 2}
        p = prio_order.get(c.get('priority', 'low'), 2)
        od = -days_overdue(c.get('dueDate', None))
        return (p, od)

    active.sort(key=sort_key)

    # Header
    header = make_project_header(project_name, meeting_time, len(active), styles)
    elements.append(Spacer(1, 16))
    elements.append(header)

    # Meeting time
    elements.append(Paragraph(
        f"<b>Meeting:</b> {meeting_time} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Open Constraints:</b> {len(active)} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Total Tracked:</b> {len(constraints)}",
        styles['MeetingTime']
    ))
    elements.append(Spacer(1, 4))

    if not active:
        elements.append(Paragraph(
            "No open constraints tracked in ConstraintsPro for this project. Consider asking: Are there any emerging risks or blockers not yet logged?",
            styles['NoConstraints']
        ))
        elements.append(Spacer(1, 8))
        return elements

    # Group by discipline
    disciplines = {}
    for c in active:
        d = c.get('discipline', 'Other')
        if d not in disciplines:
            disciplines[d] = []
        disciplines[d].append(c)

    q_num = 1
    for discipline, disc_constraints in disciplines.items():
        elements.append(Paragraph(f"<b>{discipline}</b> ({len(disc_constraints)} open)", styles['CategoryHeader']))

        for c in disc_constraints:
            desc = c.get('description', 'No description')
            priority = c.get('priority', 'unknown').upper()
            status = c.get('status', 'unknown')
            due_ms = c.get('dueDate', None)
            overdue = days_overdue(due_ms)
            last_note = get_last_note(c.get('notes', ''))

            due_str = ""
            if due_ms:
                due_str = datetime.fromtimestamp(due_ms / 1000).strftime('%b %d')

            # Priority badge
            prio_color = {"HIGH": "#C0392B", "MEDIUM": "#E67E22", "LOW": "#2980B9"}.get(priority, "#7F8C8D")
            overdue_badge = f" — <font color='#C0392B'><b>OVERDUE {overdue}d</b></font>" if overdue > 0 else ""

            # Constraint description
            elements.append(Paragraph(
                f"<font color='{prio_color}'><b>[{priority}]</b></font> "
                f"<b>{desc[:120]}</b>{overdue_badge}",
                styles['ConstraintDesc']
            ))

            # Meta line
            meta_parts = []
            if status == 'in_progress':
                meta_parts.append("Status: In Progress")
            else:
                meta_parts.append("Status: Open")
            if due_str:
                meta_parts.append(f"Due: {due_str}")
            if last_note:
                meta_parts.append(f"Latest: {last_note[:140]}")
            elements.append(Paragraph(" | ".join(meta_parts), styles['ConstraintMeta']))

            # Generate and add questions
            questions = generate_questions(c)
            for q in questions:
                text = q.get('text', '')
                urgent = q.get('urgent', False)
                context = q.get('context', None)

                if urgent:
                    prefix = '<font color="#E74C3C"><b>[ASK]</b></font> '
                    elements.append(Paragraph(f"{q_num}. {prefix}{text}", styles['UrgentQuestion']))
                else:
                    elements.append(Paragraph(f"{q_num}. {text}", styles['QuestionText']))

                if context:
                    elements.append(Paragraph(f"Why: {context}", styles['ContextNote']))
                q_num += 1

            elements.append(Spacer(1, 4))

    elements.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GRAY))
    return elements


def build_pdf():
    """Build the complete PDF document."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    doc = SimpleDocTemplate(
        OUTPUT_FILE,
        pagesize=letter,
        rightMargin=0.65 * inch,
        leftMargin=0.65 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = build_styles()
    story = []

    # Load constraint data
    with open('/tmp/constraints_by_project.json') as f:
        all_constraints = json.load(f)

    # === TITLE BLOCK ===
    story.append(Spacer(1, 30))
    story.append(HRFlowable(width="80%", thickness=2, color=NAVY))
    story.append(Spacer(1, 14))
    story.append(Paragraph("DSC Meeting Prep", styles['DocTitle']))
    story.append(Paragraph("Probing Questions by Project", styles['DocTitle']))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Live data from ConstraintsPro &bull; Ordered by meeting time",
        styles['DocSubtitle']
    ))
    story.append(Paragraph("March 3, 2026", styles['DocDate']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="80%", thickness=2, color=NAVY))
    story.append(Spacer(1, 18))

    # === MEETING SCHEDULE SUMMARY ===
    story.append(Paragraph("<b>Today's Meeting Schedule</b>", styles['IntroText']))
    story.append(Spacer(1, 4))

    schedule_data = [
        ["Time", "Project", "Meeting", "Open Constraints"],
        ["11:00 AM", "Tehuacana Creek", "Weekly Constraints", str(len([c for c in all_constraints.get('Tehuacana', []) if c.get('status') in ('open', 'in_progress')]))],
        ["11:00 AM", "Duffy BESS", "Weekly Constraints", "Not in ConstraintsPro"],
        ["11:30 AM", "Pecan Prairie", "Weekly Engineering", str(len([c for c in all_constraints.get('Pecan Prairie', []) if c.get('status') in ('open', 'in_progress')]))],
        ["12:00 PM", "Duff Solar", "NEW Constraints Meeting", str(len([c for c in all_constraints.get('DUFF', []) if c.get('status') in ('open', 'in_progress')]))],
        ["1:00 PM", "Pecan Prairie", "N&S Weekly Constraints", "—"],
        ["2:00 PM", "Scioto Ridge", "Weekly", str(len([c for c in all_constraints.get('Scioto', []) if c.get('status') in ('open', 'in_progress')]))],
        ["2:30 PM", "Mayes", "Constraints", str(len([c for c in all_constraints.get('Mayes', []) if c.get('status') in ('open', 'in_progress')]))],
        ["3:00 PM", "Delta Bobcat", "Constraints", str(len([c for c in all_constraints.get('Delta Bobcat', []) if c.get('status') in ('open', 'in_progress')]))],
    ]

    sched_table = Table(schedule_data, colWidths=[1.0*inch, 1.8*inch, 2.2*inch, 1.6*inch])
    sched_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 1), (-1, 1), HexColor("#EBF5FB")),
        ('BACKGROUND', (0, 2), (-1, 2), LIGHT_GRAY),
        ('BACKGROUND', (0, 3), (-1, 3), HexColor("#EBF5FB")),
        ('BACKGROUND', (0, 4), (-1, 4), LIGHT_GRAY),
        ('BACKGROUND', (0, 5), (-1, 5), HexColor("#EBF5FB")),
        ('BACKGROUND', (0, 6), (-1, 6), LIGHT_GRAY),
        ('BACKGROUND', (0, 7), (-1, 7), HexColor("#EBF5FB")),
        ('BACKGROUND', (0, 8), (-1, 8), LIGHT_GRAY),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, MED_GRAY),
    ]))
    story.append(sched_table)

    # Total stats
    total_open = sum(
        len([c for c in constraints if c.get('status') in ('open', 'in_progress')])
        for constraints in all_constraints.values()
    )
    total_high = sum(
        len([c for c in constraints if c.get('status') in ('open', 'in_progress') and c.get('priority') == 'high'])
        for constraints in all_constraints.values()
    )
    total_overdue = sum(
        len([c for c in constraints if c.get('status') in ('open', 'in_progress') and days_overdue(c.get('dueDate')) > 0])
        for constraints in all_constraints.values()
    )

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"<b>Portfolio Totals:</b> {total_open} open constraints | "
        f"<font color='#C0392B'><b>{total_high} high priority</b></font> | "
        f"<font color='#E67E22'><b>{total_overdue} overdue</b></font>",
        styles['IntroText']
    ))

    # Legend
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "<font color='#E74C3C'><b>[ASK]</b></font> = Priority question to raise on the call &nbsp;&nbsp;|&nbsp;&nbsp; "
        "<font color='#C0392B'><b>[HIGH]</b></font> = High priority constraint &nbsp;&nbsp;|&nbsp;&nbsp; "
        "<font color='#E67E22'><b>[MEDIUM]</b></font> = Medium &nbsp;&nbsp;|&nbsp;&nbsp; "
        "<font color='#2980B9'><b>[LOW]</b></font> = Low/Monitor",
        styles['IntroText']
    ))

    story.append(PageBreak())

    # === PROJECT SECTIONS (ordered by meeting time) ===
    projects_ordered = [
        ("Tehuacana Creek", "11:00 AM — Weekly Constraints", all_constraints.get('Tehuacana', [])),
        ("Duffy BESS", "11:00 AM — Weekly Constraints", []),
        ("Pecan Prairie", "11:30 AM Eng + 1:00 PM Constraints", all_constraints.get('Pecan Prairie', [])),
        ("Duff Solar", "12:00 PM — NEW Constraints Meeting", all_constraints.get('DUFF', [])),
        ("Scioto Ridge", "2:00 PM — Weekly", all_constraints.get('Scioto', [])),
        ("Mayes", "2:30 PM — Constraints", all_constraints.get('Mayes', [])),
        ("Delta Bobcat", "3:00 PM — Constraints", all_constraints.get('Delta Bobcat', [])),
    ]

    for proj_name, meeting_time, constraints in projects_ordered:
        section = build_project_section(proj_name, meeting_time, constraints, styles)
        story.extend(section)

    # === FOOTER ===
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=MED_GRAY))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Prepared by GOLIATH | Live data from ConstraintsPro | Generated March 3, 2026",
        styles['Footer']
    ))

    # Build
    doc.build(story)
    print(f"PDF generated: {OUTPUT_FILE}")
    return OUTPUT_FILE


if __name__ == "__main__":
    path = build_pdf()
    print(f"\nDone! File: {path}")
