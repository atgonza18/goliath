#!/usr/bin/env python3
"""
Generate DSC Portfolio Constraint Follow-Up Questions PDF
Based on Josh Hauger's 2/20/2026 Production & Constraints Email
Generated on 2026-02-25
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
import os

# Output path
OUTPUT_DIR = "/workspaces/goliath/dsc-constraints-production-reports/2026-02-25"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "2026-02-25_Portfolio_Constraint-Questions-By-Project.pdf")

# Color definitions
RED = HexColor("#C0392B")
RED_LIGHT = HexColor("#FADBD8")
RED_BG = HexColor("#F5B7B1")
ORANGE = HexColor("#E67E22")
ORANGE_LIGHT = HexColor("#FDEBD0")
ORANGE_BG = HexColor("#F5CBA7")
GREEN = HexColor("#27AE60")
GREEN_LIGHT = HexColor("#D5F5E3")
GREEN_BG = HexColor("#ABEBC6")
DARK_GRAY = HexColor("#2C3E50")
MED_GRAY = HexColor("#7F8C8D")
LIGHT_GRAY = HexColor("#ECF0F1")
WHITE = colors.white
BLACK = colors.black
URGENT_RED = HexColor("#E74C3C")
URGENT_BG = HexColor("#FDEDEC")


def build_styles():
    """Build all paragraph styles used in the document."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='DocTitle',
        fontName='Helvetica-Bold',
        fontSize=22,
        leading=28,
        alignment=TA_CENTER,
        textColor=DARK_GRAY,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='DocSubtitle',
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        alignment=TA_CENTER,
        textColor=MED_GRAY,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='DocDate',
        fontName='Helvetica-Oblique',
        fontSize=11,
        leading=14,
        alignment=TA_CENTER,
        textColor=MED_GRAY,
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name='ProjectHeader',
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=22,
        textColor=WHITE,
        spaceAfter=0,
        spaceBefore=0,
        leftIndent=8,
    ))
    styles.add(ParagraphStyle(
        name='SectionLabel',
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=DARK_GRAY,
        spaceBefore=10,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='QuestionText',
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=DARK_GRAY,
        leftIndent=20,
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name='UrgentQuestion',
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13,
        textColor=URGENT_RED,
        leftIndent=20,
        spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name='ContextNote',
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        leading=11,
        textColor=MED_GRAY,
        leftIndent=30,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='IntroText',
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=DARK_GRAY,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='CategoryHeader',
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=DARK_GRAY,
        spaceBefore=8,
        spaceAfter=4,
        leftIndent=6,
    ))
    styles.add(ParagraphStyle(
        name='Footer',
        fontName='Helvetica-Oblique',
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        textColor=MED_GRAY,
    ))
    return styles


def make_project_header(name, status, color, bg_color, styles):
    """Create a colored header bar for a project section."""
    status_map = {
        "CRITICAL": RED,
        "AT RISK": ORANGE,
        "ON TRACK": GREEN,
    }
    bg_map = {
        "CRITICAL": RED,
        "AT RISK": ORANGE,
        "ON TRACK": GREEN,
    }

    header_text = f"{name} — {status}"
    header_para = Paragraph(header_text, styles['ProjectHeader'])

    header_table = Table(
        [[header_para]],
        colWidths=[7.0 * inch],
        rowHeights=[32],
    )
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg_map.get(status, DARK_GRAY)),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ROUNDEDCORNERS', [4, 4, 4, 4]),
    ]))
    return header_table


def make_question(num, text, urgent, styles, context=None):
    """Create a question paragraph, optionally with urgent styling and context."""
    elements = []
    if urgent:
        prefix = '<font color="#E74C3C"><b>[URGENT]</b></font> '
        q_text = f"{num}. {prefix}{text}"
        elements.append(Paragraph(q_text, styles['UrgentQuestion']))
    else:
        q_text = f"{num}. {text}"
        elements.append(Paragraph(q_text, styles['QuestionText']))

    if context:
        elements.append(Paragraph(f"Context: {context}", styles['ContextNote']))

    return elements


def build_project_section(project_name, status, categories, styles):
    """Build a full project section with header and categorized questions."""
    elements = []

    color_map = {"CRITICAL": RED, "AT RISK": ORANGE, "ON TRACK": GREEN}
    bg_map = {"CRITICAL": RED_LIGHT, "AT RISK": ORANGE_LIGHT, "ON TRACK": GREEN_LIGHT}

    header = make_project_header(project_name, status, color_map[status], bg_map[status], styles)
    elements.append(Spacer(1, 14))
    elements.append(header)
    elements.append(Spacer(1, 6))

    q_num = 1
    for cat_name, questions in categories:
        elements.append(Paragraph(cat_name, styles['CategoryHeader']))

        # Light background for question block
        for q in questions:
            text = q.get("text", "")
            urgent = q.get("urgent", False)
            context = q.get("context", None)
            elems = make_question(q_num, text, urgent, styles, context)
            elements.extend(elems)
            q_num += 1

    elements.append(Spacer(1, 4))
    elements.append(HRFlowable(width="100%", thickness=0.5, color=LIGHT_GRAY))
    return elements


# =========================================================================
# PROJECT DATA — Follow-up questions organized by project
# =========================================================================

PROJECTS = [
    # ---------------------------------------------------------------
    # UNION RIDGE — CRITICAL
    # ---------------------------------------------------------------
    {
        "name": "Union Ridge",
        "status": "CRITICAL",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Modules were at 99% with forecasted completion of 2/22 — three days ago. Has module installation been completed, or is it still blocked by Constraint #1 (1,200 missing 610-style modules)?",
                    "urgent": True,
                    "context": "Completion was forecast for 2/22; we are now 3 days past that date."
                },
                {
                    "text": "The 1,200 replacement modules (610 style) were scheduled for delivery by 2/26 — that is tomorrow. Has the shipment been confirmed as on-track? Do we have a carrier/tracking number?",
                    "urgent": True,
                    "context": "Delivery was promised by 2/26; any slip directly delays SC."
                },
                {
                    "text": "AG Electrical was forecast to complete by 2/27. With only 2 days left, is the crew on pace to finish on time? Any resource or material gaps?",
                    "urgent": True,
                    "context": "2/27 completion is day-after-tomorrow; need real-time confirmation."
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #2: Has the Ulteig quote for the performance testing SOW been received? The update said 'early next week' as of 2/20, meaning it should be in-hand by now.",
                    "urgent": True,
                    "context": "'Early next week' from 2/20 = 2/23-2/24. Quote should already be received."
                },
                {
                    "text": "If Ulteig has provided the quote, what is the timeline to execute the performance testing contract before SC?",
                    "urgent": False,
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "EAC was updated with 2/7 actuals and the team was 'evaluating opportunities to cover remaining project costs.' What specific opportunities have been identified, and what is the current cost gap?",
                    "urgent": False,
                },
                {
                    "text": "With the project nearing completion, when will the next EAC update incorporate more recent actuals (post 2/7)?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # DUFF — CRITICAL
    # ---------------------------------------------------------------
    {
        "name": "Duff",
        "status": "CRITICAL",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles dropped to 0/day from 115/day — what caused the complete stoppage? When will pile driving resume, and what is the recovery plan to reach the required 155/day?",
                    "urgent": True,
                    "context": "0/day production for piles is a full work stoppage; every day lost compounds schedule risk."
                },
                {
                    "text": "Racking also dropped to 0/day from 150/day — is this related to the pile stoppage, or is there a separate issue? What is the plan to ramp to the required 375/day?",
                    "urgent": True,
                    "context": "375/day required vs. 0/day actual — a 100% gap with no apparent recovery path."
                },
                {
                    "text": "UG Electrical forecast shows completion by 6/26 — is this aligned with the overall project schedule, or does it represent a delay?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: B&E was performing a 3rd-party schedule review as of 2/20. Has that review been completed? What are the findings, and is there now approved schedule relief from the owner?",
                    "urgent": True,
                    "context": "The schedule is being pushed months with no approved relief — this is the #1 commercial risk."
                },
                {
                    "text": "Constraint #2: The ultimatum was sent to J&B regarding LD risk. Have they responded? Are we prepared to execute a backup subcontractor plan if J&B walks?",
                    "urgent": True,
                    "context": "J&B removed all LD risk from their redlines; unresolved subcontractor risk."
                },
                {
                    "text": "Constraint #3: Geomatics sent maps of the ~6,000 out-of-tolerance piles to the site team. Has the site team completed their review? What is the remediation plan and estimated duration/cost?",
                    "urgent": True,
                    "context": "6,000 piles with reveal height issues is a massive remediation scope."
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "The project is carrying $5M+ commercial risk AND $8-10M productivity risk tied to mechanical remid delays. What is the current total EAC variance, and what mitigation actions are reducing these figures?",
                    "urgent": True,
                    "context": "Combined $13-15M risk exposure demands executive-level attention."
                },
                {
                    "text": "What portion of the $8-10M productivity risk is directly attributable to the ~6,000 out-of-tolerance piles? Is the remediation being captured in the EAC?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # SALT BRANCH — CRITICAL
    # ---------------------------------------------------------------
    {
        "name": "Salt Branch",
        "status": "CRITICAL",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles dropped from 500/day to 175/day with 1,300/day required. What caused the 65% decline, and what specific actions are being taken to recover?",
                    "urgent": True,
                    "context": "At 175/day vs 1,300/day required, the project needs a 7.4x production increase."
                },
                {
                    "text": "Racking dropped from 25/day to 18/day with 45/day required, and is on hold due to safety manpower (Constraint #1). When will the safety hold be lifted?",
                    "urgent": True,
                    "context": "Racking and modules both on hold — no production until safety support is resolved."
                },
                {
                    "text": "Modules dropped from 1,600/day to 1,300/day with 7,800/day required. Even before the safety hold, rates were only 17% of what is needed. What is the realistic recovery plan?",
                    "urgent": True,
                    "context": "7,800/day requirement vs 1,300/day actual — a 6x gap even without the hold."
                },
                {
                    "text": "UG Electrical forecast shows DC completion by June 2026. Is this aligned with the overall schedule, and are there any resource constraints on the electrical crew?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: What is the status of coordination with the safety department? Have additional safety personnel been identified and committed? What is the expected date to resume racking and module installation?",
                    "urgent": True,
                    "context": "Every day of safety-hold is a day of zero racking/module production."
                },
                {
                    "text": "Constraint #2: What are the specific remediation manpower and tooling requirements identified? Have additional resources been mobilized or contracted?",
                    "urgent": True,
                    "context": "Block turnover to racking is the gating predecessor — delays cascade to everything downstream."
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "The project carries $4M+ commercial risk related to predrill activities. What is the root cause, and is this a change order opportunity or a pure cost overrun?",
                    "urgent": False,
                },
                {
                    "text": "Given the production rate gaps across piles, racking, and modules, what is the updated productivity cost impact beyond the $4M predrill risk?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # BLACKFORD — CRITICAL
    # ---------------------------------------------------------------
    {
        "name": "Blackford",
        "status": "CRITICAL",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles are at 970/day vs. 1,500/day required (65% of target). What is the plan to close the 530/day gap? Are additional pile crews or rigs available?",
                    "urgent": False,
                },
                {
                    "text": "Modules jumped from 115/day to 330/day — positive trend, but 6,500/day is required. What is the realistic ramp-up plan, and when will module crews reach full capacity?",
                    "urgent": False,
                },
                {
                    "text": "Racking is at 55/day vs. 75/day required. Is the 27% gap due to crew limitations, material availability, or block turnover constraints?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: Poles for the T-Line/MV line conflict started arriving on 2/20. Have all poles been received? Has Nello provided their final response on the conflict resolution?",
                    "urgent": True,
                    "context": "Poles started arriving 5 days ago; Nello response was still pending."
                },
                {
                    "text": "Constraint #2: The next 4 blocks of surface files were sent to EOR as of 2/20. Have the PPPs been finalized? Pile installation going out-of-block is a direct schedule risk.",
                    "urgent": True,
                    "context": "PPP delays push pile work out of planned blocks, creating rework and inefficiency."
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "The project carries $5M+ LD risk with the schedule pushing SC 6-7 weeks. What specific schedule acceleration measures are being evaluated to reduce LD exposure?",
                    "urgent": True,
                    "context": "$5M+ LD risk is directly tied to schedule recovery — every week saved reduces exposure."
                },
                {
                    "text": "Is there an active change order or schedule relief claim in progress with the owner to mitigate the 6-7 week SC push?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # DELTA BOBCAT — AT RISK
    # ---------------------------------------------------------------
    {
        "name": "Delta Bobcat",
        "status": "AT RISK",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles are at 1,400/day vs. 285/day required — well ahead. Is the pile crew being right-sized, or can excess capacity be redeployed to other projects?",
                    "urgent": False,
                },
                {
                    "text": "Racking increased from 95/day to 120/day but requires 305/day. What is preventing a faster ramp-up given that piles are well ahead of schedule?",
                    "urgent": False,
                },
                {
                    "text": "Modules increased from 100/day to 130/day but require 2,650/day. This is a 20x gap. Is this a crew mobilization timing issue, or are there material/predecessor constraints?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: The county vote on jump bridge approval was expected 'next week' as of 2/20 — that means this week. Has the vote occurred? What was the result?",
                    "urgent": True,
                    "context": "County vote was expected week of 2/23; result should be known by now."
                },
                {
                    "text": "If jump bridges are not approved, what is the fallback plan for inverter installation, and how much schedule delay would that cause?",
                    "urgent": False,
                },
                {
                    "text": "Constraint #2: Additional mats are being pursued to address ground conditions impacting racking. Have the mats been secured? What is the expected delivery timeline?",
                    "urgent": False,
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "No significant cost risks reported — is this still the case given the large production gaps on racking and modules? Are there acceleration costs being considered?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # THREE RIVERS — AT RISK
    # ---------------------------------------------------------------
    {
        "name": "Three Rivers",
        "status": "AT RISK",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles increased from 25/day to 60/day but require 565/day — still only 11% of target. What is the primary bottleneck, and when do we expect meaningful acceleration?",
                    "urgent": True,
                    "context": "At current rates, pile completion would take roughly 9x longer than scheduled."
                },
                {
                    "text": "Racking is being onboarded to the production model next week (as of 2/20). Has the onboarding occurred? What are the initial racking production forecasts?",
                    "urgent": False,
                    "context": "'Next week' from 2/20 = this week (2/23-2/27)."
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: Pile testing requirements were increased, impacting remediation. Has the team finalized the approach for motor piles with increased requirements? What is the impact on the remediation schedule?",
                    "urgent": True,
                    "context": "Increased testing requirements with no clear resolution path slows all pile-dependent work."
                },
                {
                    "text": "What options have been identified for 'safe testing operations,' and which option is the team recommending?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # SCIOTO RIDGE — AT RISK
    # ---------------------------------------------------------------
    {
        "name": "Scioto Ridge",
        "status": "AT RISK",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "PV grading has been at 0/day for 4+ weeks (now 5+ weeks since we are 5 days past the email). When does the team expect ground conditions to allow resumption of civil work?",
                    "urgent": True,
                    "context": "5+ weeks of zero civil production — schedule impact is compounding daily."
                },
                {
                    "text": "Is there a weather/ground condition forecast that gives us a target restart date for civil, electrical, and pile work?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "The team met with the civil FOM group and developed a plan to navigate ground conditions. What is that plan, and has execution begun?",
                    "urgent": True,
                    "context": "A plan was developed as of 2/20 — 5 days later, we need to know if it is being executed."
                },
                {
                    "text": "Does the plan include any specialized equipment, matting, or subcontractor support? If so, have those resources been procured?",
                    "urgent": False,
                },
                {
                    "text": "What is the updated schedule forecast for civil completion now that we have lost 5+ weeks?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # MAYES — AT RISK
    # ---------------------------------------------------------------
    {
        "name": "Mayes",
        "status": "AT RISK",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Piles increased from 250/day to 375/day but require 580/day. What additional rigs or crews are needed to close the remaining 205/day gap?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: County crossing permits were approved on 2/20 for bore access under county roads — excellent. What about the utility crossing permits? What is the expected approval timeline?",
                    "urgent": True,
                    "context": "County permits approved, but utility permits still outstanding — partial resolution only."
                },
                {
                    "text": "Constraint #2: The first 4 blocks of PPPs were received, with the next set expected 'next week' (from 2/20). Has the next batch of PPPs been delivered?",
                    "urgent": True,
                    "context": "'Next week' from 2/20 means this week. PPPs gate pile production acceleration."
                },
                {
                    "text": "Constraint #3: Shoals delivery schedule threatens the accelerated LRE May COD commitment. Has procurement made any progress accelerating the Shoals delivery? What is the current expected delivery date vs. what is needed?",
                    "urgent": True,
                    "context": "May COD is a hard commitment — any Shoals delay directly impacts COD."
                },
                {
                    "text": "Is there a contingency plan if Shoals cannot accelerate? Are alternative suppliers or partial shipments being explored?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # GRACELAND — ON TRACK
    # ---------------------------------------------------------------
    {
        "name": "Graceland",
        "status": "ON TRACK",
        "categories": [
            ("Production Follow-Ups", [
                {
                    "text": "Electrical is at 89% with mid-March completion forecast. Is the crew still on pace, and are there any emerging risks in the final 11%?",
                    "urgent": False,
                },
            ]),
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: Shoals planned to ship the string wire order on 2/24 — yesterday. Has the shipment been confirmed? Do we have tracking and an ETA to site?",
                    "urgent": True,
                    "context": "Ship date was 2/24 (yesterday). If it did not ship, electrical completion is at risk."
                },
                {
                    "text": "Once the string wire arrives, how many days of production are needed to complete the remaining electrical scope?",
                    "urgent": False,
                },
            ]),
            ("Cost Risk Follow-Ups", [
                {
                    "text": "The ~$0.5M cost risk is primarily electrical. With 11% of electrical scope remaining, is the risk being actively managed down, or is it expected to materialize?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # TEHUACANA — ON TRACK
    # ---------------------------------------------------------------
    {
        "name": "Tehuacana",
        "status": "ON TRACK",
        "categories": [
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: The 6 PD-10 rentals did not have Carlson systems installed. Has the equipment team ordered the required parts? What is the expected install timeline?",
                    "urgent": True,
                    "context": "PD-10 availability is the critical constraint; parts ordering should have happened by now."
                },
                {
                    "text": "Are the PD-10s usable for any pile driving operations without the Carlson system, or are they completely idle until parts arrive?",
                    "urgent": False,
                },
                {
                    "text": "With 11 open constraints and only 1 closed, what is the plan to start closing constraints at a faster rate?",
                    "urgent": False,
                },
            ]),
        ]
    },

    # ---------------------------------------------------------------
    # PECAN PRAIRIE — ON TRACK
    # ---------------------------------------------------------------
    {
        "name": "Pecan Prairie",
        "status": "ON TRACK",
        "categories": [
            ("Constraint Follow-Ups", [
                {
                    "text": "Constraint #1: The team received the permit schedule from Repsol this week (as of 2/20). Does the Repsol permit schedule align with our construction schedule, or are there gaps that need to be escalated?",
                    "urgent": False,
                },
                {
                    "text": "Constraint #2: Awaiting Repsol's formal response on the Acceleration CO. Has the response been received? What is the financial exposure if the CO is not approved?",
                    "urgent": True,
                    "context": "Acceleration CO poses 'significant upfront cost risk' — Repsol response is overdue."
                },
                {
                    "text": "With 28 open constraints vs. only 14 closed, this is the highest open constraint count in the portfolio. What is the constraint closure plan, and which constraints are highest priority?",
                    "urgent": False,
                },
            ]),
        ]
    },
]


def build_pdf():
    """Build the complete PDF document."""
    doc = SimpleDocTemplate(
        OUTPUT_FILE,
        pagesize=letter,
        rightMargin=0.7 * inch,
        leftMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = build_styles()
    story = []

    # --- TITLE BLOCK ---
    story.append(Spacer(1, 40))
    story.append(HRFlowable(width="80%", thickness=2, color=DARK_GRAY))
    story.append(Spacer(1, 16))
    story.append(Paragraph("DSC Portfolio", styles['DocTitle']))
    story.append(Paragraph("Constraint Follow-Up Questions by Project", styles['DocTitle']))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Based on Josh Hauger's 2/20/2026 Production &amp; Constraints Email",
        styles['DocSubtitle']
    ))
    story.append(Paragraph("February 25, 2026", styles['DocDate']))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="80%", thickness=2, color=DARK_GRAY))
    story.append(Spacer(1, 24))

    # --- INTRO / LEGEND ---
    story.append(Paragraph(
        "This document contains targeted follow-up questions for each project in the DSC solar construction portfolio. "
        "Questions are organized by project and categorized into Production, Constraints, and Cost Risk follow-ups. "
        "Items marked <font color='#E74C3C'><b>[URGENT]</b></font> indicate questions where deadlines have passed, "
        "commitments are due, or production has stopped — these require immediate answers.",
        styles['IntroText']
    ))
    story.append(Spacer(1, 4))

    # Legend table
    legend_data = [
        [
            Paragraph("<b>Legend:</b>", styles['IntroText']),
            Paragraph('<font color="#C0392B"><b>CRITICAL</b></font> — Major schedule/cost risk', styles['IntroText']),
            Paragraph('<font color="#E67E22"><b>AT RISK</b></font> — Emerging risks', styles['IntroText']),
            Paragraph('<font color="#27AE60"><b>ON TRACK</b></font> — Progressing normally', styles['IntroText']),
        ]
    ]
    legend_table = Table(legend_data, colWidths=[0.8 * inch, 2.2 * inch, 1.8 * inch, 2.2 * inch])
    legend_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(legend_table)
    story.append(Spacer(1, 6))

    # Summary count
    critical_count = sum(1 for p in PROJECTS if p["status"] == "CRITICAL")
    at_risk_count = sum(1 for p in PROJECTS if p["status"] == "AT RISK")
    on_track_count = sum(1 for p in PROJECTS if p["status"] == "ON TRACK")
    total_questions = sum(
        sum(len(qs) for _, qs in p["categories"])
        for p in PROJECTS
    )
    urgent_count = sum(
        sum(1 for q in qs if q.get("urgent"))
        for p in PROJECTS
        for _, qs in p["categories"]
    )

    story.append(Paragraph(
        f"<b>Portfolio Summary:</b> {len(PROJECTS)} projects "
        f"({critical_count} Critical, {at_risk_count} At Risk, {on_track_count} On Track) | "
        f"{total_questions} total questions | "
        f"<font color='#E74C3C'><b>{urgent_count} urgent</b></font>",
        styles['IntroText']
    ))

    story.append(PageBreak())

    # --- PROJECT SECTIONS ---
    for project in PROJECTS:
        section = build_project_section(
            project["name"],
            project["status"],
            project["categories"],
            styles,
        )
        story.extend(section)

    # --- FOOTER NOTE ---
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=MED_GRAY))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        "Prepared by DSC Constraints Management | Source: Josh Hauger's 2/20/2026 Production &amp; Constraints Email | "
        "Questions generated 2/25/2026 — 5 days after source data",
        styles['Footer']
    ))

    # Build PDF
    doc.build(story)
    print(f"PDF generated successfully: {OUTPUT_FILE}")


if __name__ == "__main__":
    build_pdf()
