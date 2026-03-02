#!/usr/bin/env python3
"""Generate Blackford Solar HIGH PRIORITY ONLY probing questions PDF - concise, factual, actionable."""
from fpdf import FPDF
import os, re
from datetime import datetime

def clean(text):
    """Replace unicode chars that Helvetica can't handle."""
    replacements = {
        '\u2014': '--', '\u2013': '-', '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u2022': '-',
        '\u2192': '->', '\u2190': '<-', '\u2191': '^', '\u2193': 'v',
        '\u2713': '[x]', '\u2717': '[!]', '\u00b7': '-',
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    # Strip any remaining non-latin1 chars
    text = text.encode('latin-1', errors='replace').decode('latin-1')
    return text

class ProbingQuestionsPDF(FPDF):
    def cell(self, *args, **kwargs):
        if args and len(args) >= 3 and isinstance(args[2], str):
            args = list(args)
            args[2] = clean(args[2])
            args = tuple(args)
        if 'text' in kwargs and isinstance(kwargs['text'], str):
            kwargs['text'] = clean(kwargs['text'])
        return super().cell(*args, **kwargs)

    def multi_cell(self, *args, **kwargs):
        if args and len(args) >= 3 and isinstance(args[2], str):
            args = list(args)
            args[2] = clean(args[2])
            args = tuple(args)
        if 'text' in kwargs and isinstance(kwargs['text'], str):
            kwargs['text'] = clean(kwargs['text'])
        return super().multi_cell(*args, **kwargs)

    def header(self):
        self.set_font('Helvetica', 'B', 11)
        self.set_text_color(180, 30, 30)
        self.cell(0, 6, 'BLACKFORD SOLAR — HIGH PRIORITY CONSTRAINT QUESTIONS', 0, 1, 'C')
        self.set_font('Helvetica', '', 9)
        self.set_text_color(80, 80, 80)
        self.cell(0, 5, 'February 27, 2026 | Data Date: Feb 23 | Prepared by Goliath / DSC', 0, 1, 'C')
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Blackford Solar - HIGH Priority Constraints | Page {self.page_no()}/{{nb}}', 0, 0, 'C')

    def section_header(self, title, color=(180, 30, 30)):
        self.set_font('Helvetica', 'B', 12)
        self.set_text_color(*color)
        self.cell(0, 8, title, 0, 1)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def constraint_block(self, number, title, status, owner, need_by, discipline,
                         facts, latest_notes, schedule_impact, questions):
        # Red header bar
        self.set_fill_color(180, 30, 30)
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 8, f'  {number}. {title}', 1, 1, 'L', True)
        self.set_text_color(0, 0, 0)
        self.ln(2)

        # Metadata
        self.set_font('Helvetica', 'B', 9)
        self.cell(18, 5, 'Owner:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(50, 5, owner, 0, 0)
        self.set_font('Helvetica', 'B', 9)
        self.cell(22, 5, 'Need-By:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(35, 5, need_by, 0, 0)
        self.set_font('Helvetica', 'B', 9)
        self.cell(18, 5, 'Status:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(0, 5, status, 0, 1)

        self.set_font('Helvetica', 'B', 9)
        self.cell(22, 5, 'Discipline:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(0, 5, discipline, 0, 1)
        self.ln(2)

        # Key Facts
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(0, 0, 120)
        self.cell(0, 5, 'KEY FACTS:', 0, 1)
        self.set_text_color(0, 0, 0)
        self.set_font('Helvetica', '', 8)
        for fact in facts:
            self.cell(5, 4, '', 0, 0)
            bullet_text = f"• {fact}"
            self.multi_cell(185, 4, bullet_text)
        self.ln(1)

        # Latest Notes from ConstraintsPro
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, 'LATEST CONSTRAINTSPRO NOTES:', 0, 1)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(60, 60, 60)
        for note in latest_notes:
            self.cell(5, 4, '', 0, 0)
            self.multi_cell(185, 4, note)
        self.set_text_color(0, 0, 0)
        self.ln(1)

        # Schedule Impact
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(180, 30, 30)
        self.cell(0, 5, 'SCHEDULE IMPACT:', 0, 1)
        self.set_font('Helvetica', '', 8)
        self.set_text_color(0, 0, 0)
        self.multi_cell(0, 4, schedule_impact)
        self.ln(2)

        # Probing Questions
        self.set_font('Helvetica', 'B', 9)
        self.set_text_color(0, 100, 0)
        self.cell(0, 5, 'PROBING QUESTIONS:', 0, 1)
        self.set_text_color(0, 0, 0)
        for i, q in enumerate(questions, 1):
            self.set_font('Helvetica', 'B', 9)
            self.cell(10, 5, f'Q{i}:', 0, 0)
            self.set_font('Helvetica', '', 9)
            self.multi_cell(180, 5, q)
            self.ln(1)

        self.ln(4)


pdf = ProbingQuestionsPDF()
pdf.alias_nb_pages()
pdf.add_page()

# ===== EXECUTIVE SNAPSHOT =====
pdf.set_font('Helvetica', 'B', 12)
pdf.set_text_color(0, 0, 0)
pdf.cell(0, 8, 'PROJECT SNAPSHOT', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Blackford Solar | 211 MW DC | 38 Blocks | Schedule Data Date: Feb 23, 2026\n"
    "Overall Critical Path Float: -53 days | Backfeed Target: Jun 1, 2026 (-94 days float)\n"
    "COD Target: Aug 6, 2026 (-53 days float) | First Circuit MC: Mar 25, 2026 (79 days late)\n\n"
    "5 HIGH priority constraints are active. All 5 are overdue or at immediate risk. The substation/T-line "
    "path is the project-killer at -94 days float. Pile production is about to stall waiting on Timmons. "
    "Every day of delay on these items compounds the overall deficit."
)
pdf.ln(4)

# ===== CONSTRAINT 1: PV GRADING =====
pdf.constraint_block(
    1,
    "PV GRADING DELAYS — 5 Blocks Stuck",
    "IN PROGRESS — OVERDUE (need-by was Nov 17, 2025)",
    "Ross",
    "Nov 17, 2025 (103 DAYS OVERDUE)",
    "Civil Engineering",
    facts=[
        "5 blocks remain: 33, 35, 21, 22, 25 — stuck since December 2025",
        "Ground conditions (moisture, frozen ground — Indiana winter) cited as blocker every week",
        "Strip topsoil for Blocks 25, 21, 22 still outstanding (13.5 days remaining)",
        "Drain tile added scope: 24 remaining days, complicates grading sequence",
        "PV array grading has 14 remaining days of work",
        "Ross stated '2 week catchup time with full working hours' on 2/25",
    ],
    latest_notes=[
        "2/25: Ground conditions still not suitable. 5 blocks left. 2 week catchup time.",
        "2/16: Frozen ground continues. Sand backfill approved as workaround for wet conditions.",
        "2/4: 5 blocks outstanding, same ground condition issues.",
    ],
    schedule_impact=(
        "Pile foundations: -9.5 to -36.5 days float (directly gated by grading). "
        "Array construction: -5 to -38.5 days float downstream. Module placement cannot begin "
        "until piles are in, which can't start until grading is done. Every day of grading delay "
        "is a 1:1 addition to the -53 day overall project deficit."
    ),
    questions=[
        "Ross, ground conditions have been cited every week since December — that's 3 months. What is the SPECIFIC soil moisture threshold and temperature needed for grading, and what does the 10-day forecast show? If conditions won't improve by March 10, what is Plan B — lime stabilization, geo-grid, imported fill? I need a contingency plan with cost estimate, not another week of 'waiting for weather.'",
        "You said '2 week catchup with full working hours' — spell that out for me: how many pieces of equipment, how many operators, what shifts (day only or day+night), and is all of that currently on site or does it need to be mobilized? What's the cost of that 2-week push vs. the daily cost of delay?",
        "Blocks 21, 22, and 25 are also blocked by material storage (see Constraint #5 in prior report). Even if ground conditions improve tomorrow, can we actually grade those blocks or are torque tubes and modules still in the way? What's the sequencing between material relocation and grading?",
    ]
)

pdf.add_page()

# ===== CONSTRAINT 2: PPP TURNAROUND =====
pdf.constraint_block(
    2,
    "PPP TURNAROUND — Timmons Non-Responsive",
    "IN PROGRESS — OVERDUE (need-by was Jan 19, 2026)",
    "Scott M. / Timmons (Ferdinand, Jacqueline)",
    "Jan 19, 2026 (39 DAYS OVERDUE)",
    "Piles / Engineering",
    facts=[
        "Surface files submitted to Timmons on Friday Feb 20 — 5 business days with NO response",
        "PPP = Pile Placement Plan required before piling can begin on each block",
        "Scott M. warned on 2/16: 'at current production rate, piles will run out of work'",
        "We are now 11 days past that warning with no resolution",
        "Surface file issues flagged for Block 20 north section, Block 10, Block 12",
        "Block 29 opening up for piles (3 blocks total available)",
        "Pile shakeout ends Mar 23 per schedule; pile foundations end Mar 31",
        "Jacqueline followed up morning of 2/25 — no response as of 2/27",
    ],
    latest_notes=[
        "2/25: No response yet from Timmons. Reaching out to Ferdinand. Jacqueline followed up this AM. Block 29 opening for piles.",
        "2/20: Surface files sent to Timmons for PPP review.",
        "2/16: At current production rate, piles will run out of work. Need Timmons turnaround ASAP.",
    ],
    schedule_impact=(
        "Pile foundations: -9.5 to -36.5 days float depending on block. Last pile block (Block 12) "
        "must complete by Mar 31. Pile remediation runs through Apr 15. If Timmons doesn't return "
        "PPP packages by ~Mar 3, pile crews go idle and the -36.5 day deficit on the last blocks "
        "grows. This cascades through tracker install, module placement, and electrical — adding "
        "directly to the -53 day overall project float."
    ),
    questions=[
        "Scott, surface files went to Timmons on Feb 20 and it's now Feb 27 — 5 business days, zero response. What is the CONTRACTUAL turnaround time for PPP reviews, and have we sent a formal notice that they're in breach? If not, why not? I want that notice sent today.",
        "You warned on 2/16 that piles would run out of work. We're 11 days past that. How many days of approved pile work remain in currently-released blocks, and what is the EXACT date pile crews go idle? If it's within 5 days, we need to be on the phone with Timmons leadership today — not emailing Jacqueline.",
        "Blocks 10, 12, and 20 north had surface file issues flagged. If micro-grading is required before piling, how many days does that add per block, who performs it, and has it been scheduled? We can't afford to get the PPP back and then discover we need another 2 weeks of civil work first.",
    ]
)

pdf.add_page()

# ===== CONSTRAINT 3: T-LINE POLE CONFIGURATION =====
pdf.constraint_block(
    3,
    "T-LINE POLE CONFIGURATION — PVI Approval Pending",
    "OPEN — OVERDUE (need-by was Feb 17, 2026)",
    "Nick / PVI / Nello",
    "Feb 17, 2026 (10 DAYS OVERDUE)",
    "Engineering / T-Line",
    facts=[
        "Poles are physically ON SITE — can't install without engineering sign-off",
        "Nello took 2+ weeks to produce pole configuration calcs",
        "Calcs submitted to PVI for review — PVI currently reviewing as of 2/25",
        "Nick expected 'clean response and green light from PVI on Friday' (Feb 27)",
        "Keeley return expected Wednesday (Feb 25) per notes",
        "T-Line foundations: 11 days remaining work",
        "T-Line erection: 15 days | Stringing/Sag/Clip: 15 days | Commissioning: 7 days",
        "Total remaining T-Line work: ~48 days vs. 94 days until Jun 1 backfeed",
    ],
    latest_notes=[
        "2/25: Approval from everyone that calcs work. Sent to PVI. PVI reviewing. Expected green light Friday. Hoping for Keeley return Wednesday.",
        "2/16: Nello still working on calcs. Dragging for 2+ weeks.",
        "2/4: Pole configuration conflict between Nello design and field conditions.",
    ],
    schedule_impact=(
        "T-Line at -57 days float. This is on the CRITICAL PATH to backfeed (Jun 1, -94 days float). "
        "Even with PVI approval today, 48 days of T-Line work remain against 94 calendar days to "
        "backfeed — technically achievable but with ZERO margin. Any further delay here directly "
        "pushes backfeed and potentially COD. Substation testing/commissioning (30 days) cannot "
        "start until T-Line and substation are both complete."
    ),
    questions=[
        "Nick, today is Friday Feb 27 — did PVI give the green light? If yes, when does the T-line crew start foundations and what's the committed completion date? If no, what is PVI's specific concern and who do we call to escalate — we need a name and phone number, not an email chain.",
        "Let's do the math together: 48 days of T-line work remaining, 94 calendar days to backfeed. That works IF we start Monday and hit zero delays. What's your realistic assessment — is Jun 1 backfeed still achievable for the T-line scope? If not, what date are we actually looking at and have you communicated that to the owner?",
        "Nello burned 2+ weeks on calcs that should have taken days. Now PVI is in the review loop. What's our exposure if PVI comes back with comments instead of approval — how many days of back-and-forth are we looking at, and can we have Nello on standby to turn around responses same-day?",
    ]
)

pdf.add_page()

# ===== CONSTRAINT 4: MPT DELIVERY =====
pdf.constraint_block(
    4,
    "MPT DELIVERY — Not Yet On Site",
    "OPEN — due Mar 3, 2026 (4 DAYS AWAY)",
    "Ross / Owner-Furnished",
    "Mar 3, 2026",
    "Procurement / Substation",
    facts=[
        "Main Power Transformer is owner-furnished — NOT YET DELIVERED",
        "Schedule shows delivery by Mar 6 (9 remaining days of shipping as of data date)",
        "MPT install: 5 days placement + 5 days assembly/oil fill = 10 days minimum",
        "Substation equipment install at -89 days float; bus work at -88 days float",
        "Substation testing & commissioning: May 1 through Jun 1 (30 days, no slack)",
        "Notice drafted for cost accounting on delivery delays — sent last week",
        "Follow-up notices pending per 2/25 update",
        "All other substation equipment converging Mar 2-23 (steel, switches, arresters, breakers)",
    ],
    latest_notes=[
        "2/25: Notice being drafted to get cost accounted for on delivery delays. Notice sent last week. Follow-up notices pending.",
        "2/16: MPT delivery date still uncertain. Owner responsible for delivery.",
    ],
    schedule_impact=(
        "MPT install at -65 days float. Substation overall at -89 to -94 days float. "
        "The entire substation/interconnection path is THE critical path of the project. "
        "Backfeed (Jun 1) requires: MPT delivered → installed (10 days) → substation steel/equipment "
        "complete → ground grid → control wiring → testing/commissioning (30 days). "
        "If MPT arrives even 1 week late, the Jun 1 backfeed date becomes mathematically impossible "
        "without compression of the commissioning sequence."
    ),
    questions=[
        "Ross, the MPT is owner-furnished and the schedule shows delivery by Mar 6. Is there a confirmed ship date and tracking number? If the transformer is on a truck right now, where is it and when does it arrive? If it hasn't shipped, why not and what's the new date? This is the single biggest item on the critical path.",
        "You sent a cost accounting notice last week for delivery delays. Who was it sent to, what was the response, and what is the daily cost exposure for each day the MPT is late? Has the owner acknowledged responsibility for the schedule impact?",
        "Once the MPT arrives, you need a crane for placement and a crew for the 10-day install sequence. Are both the crane and crew already contracted and scheduled, or do they need to be mobilized? If the MPT shows up Monday, can we start install Tuesday — or is there a mobilization gap?",
    ]
)

pdf.add_page()

# ===== CONSTRAINT 5: QI STAFFING =====
pdf.constraint_block(
    5,
    "QI STAFFING — Need 6, Have 0 On Site",
    "OPEN — due Mar 1, 2026 (2 DAYS AWAY)",
    "Gabryal / Bill Nichols",
    "Mar 1, 2026",
    "Quality",
    facts=[
        "Need 6 Quality Inspectors for 38-block site — currently 0 on site",
        "1 QI confirmed arriving March 5 — that still leaves 5 unfilled",
        "Gap being bridged with laborers doing inspections + field engineers covering more area",
        "Gabryal following up with Bill Nichols to 'get him some heads'",
        "Pile remediation runs through Apr 15 — requires QI oversight",
        "Array construction (tracker, modules, electrical) ramping across 30+ blocks simultaneously",
        "VOC testing starts Mar 6; PD testing starts Mar 11 — electrical QC is critical",
    ],
    latest_notes=[
        "2/25: 1 QI coming in on the 5th. Bridging gap with laborers to reduce remediation issues. FEs in field more. Will follow up with Bill Nichols for staffing.",
        "2/16: QI staffing gap persists. Using laborers as stopgap.",
    ],
    schedule_impact=(
        "Pile remediation: through Apr 15. Array construction: through May 28. "
        "Electrical testing (VOC, PD): starts Mar 6. Without QIs, the risk isn't just schedule — "
        "it's quality. Defects caught late require rework that adds time and cost. "
        "With 38 blocks in various stages and no dedicated QI presence, the probability of "
        "systemic quality issues going undetected is HIGH. This is a ticking time bomb that "
        "manifests during commissioning when it's most expensive to fix."
    ),
    questions=[
        "Gabryal, the need-by date is March 1 — that's Saturday. You have 1 person confirmed for March 5 and a conversation pending with Bill Nichols. Give me the honest answer: when will all 6 QIs be on site? I need names and dates, not 'working on it.'",
        "You're using laborers for quality inspections as a stopgap. What is the current defect/remediation rate — actual numbers? Has it gone up since the QI gap started? If you don't have that data, that's a problem in itself because it means nobody is tracking quality metrics.",
        "Electrical testing starts March 6 — VOC testing on the first blocks, PD testing March 11. These require qualified inspectors, not laborers. Who is performing electrical QC on those dates? If the answer is 'nobody qualified,' we have a stop-work risk on testing.",
    ]
)

# ===== CALL STRATEGY =====
pdf.add_page()
pdf.section_header('CALL STRATEGY & KEY DEMANDS', (0, 0, 120))
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "This project is -53 days behind schedule with EVERY major milestone negative. The substation/T-line "
    "path is at -94 days float — the worst in the portfolio. 5 of 5 HIGH constraints are overdue or "
    "at immediate risk. The pattern of weekly status updates with no resolution must end.\n\n"
    "DEMAND #1 — RECOVERY SCHEDULE\n"
    "Ask for a resource-loaded recovery schedule by end of next week. It must show: crew sizes, "
    "equipment, overtime plans, and specific dates for each milestone. 'We'll catch up when weather "
    "improves' is not a plan.\n\n"
    "DEMAND #2 — DAILY CONSTRAINT CADENCE\n"
    "HIGH priority constraints get daily updates. Not weekly. The gaps between notes (2/16 to 2/25 = "
    "9 days with no update) are unacceptable on a project running -53 days.\n\n"
    "DEMAND #3 — THIRD-PARTY ACCOUNTABILITY\n"
    "Timmons (PPP), PVI (T-line), Nello (calcs), Owner (MPT): each has commitments that are late. "
    "For each: what is the contractual obligation, when did we notify them of breach, and what is "
    "the escalation plan if they miss again?\n\n"
    "DEMAND #4 — COST EXPOSURE MODEL\n"
    "With -53 to -94 days negative float, LDs and extended general conditions are a real financial "
    "risk. Has anyone quantified the total cost exposure? If not, that analysis needs to happen this "
    "week. The owner will ask, and we need to be ready.\n\n"
    "DEMAND #5 — QI STAFFING PLAN TIED TO PRODUCTION CURVE\n"
    "Production is ramping. Quality oversight is flat at zero. This gap will show up during "
    "commissioning as rework and punch list items. Need a staffing plan that scales with production."
)

# ===== FOLLOW-UP EMAIL DRAFTS =====
pdf.add_page()
pdf.section_header('FOLLOW-UP EMAIL DRAFTS (Ready to Send)', (0, 0, 120))

# Timmons email
pdf.set_font('Helvetica', 'B', 10)
pdf.set_fill_color(240, 240, 240)
pdf.cell(0, 6, 'TO: Ferdinand / Jacqueline — Timmons (PPP Turnaround)', 1, 1, 'L', True)
pdf.set_font('Helvetica', '', 8)
pdf.multi_cell(0, 4,
    "Subject: URGENT — Blackford Solar PPP Review — 5 Business Days Without Response\n\n"
    "Ferdinand / Jacqueline,\n\n"
    "Surface files were submitted for PPP review on Friday February 20. As of today (Feb 27), "
    "we have received no response — not even an acknowledgment of receipt. Our pile crews are "
    "running out of approved work blocks and face idle time within days.\n\n"
    "We need by EOD today:\n"
    "1. Confirmation you received and are reviewing the surface files\n"
    "2. Expected return date for the PPP packages\n"
    "3. Any issues with Block 10, Block 12, or Block 20 north section files\n\n"
    "If we do not hear back by EOD, we will escalate to your management and formally document "
    "the schedule impact as a Timmons-caused delay.\n\n"
    "Regards"
)
pdf.ln(3)

# PVI email
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'TO: PVI — T-Line Pole Configuration Approval', 1, 1, 'L', True)
pdf.set_font('Helvetica', '', 8)
pdf.multi_cell(0, 4,
    "Subject: Blackford Solar — T-Line Pole Calcs Review — Response Needed Today\n\n"
    "Team,\n\n"
    "Nello submitted pole configuration calculations for your review. All poles are physically on "
    "site and the T-line crew is standing by. The T-line schedule is at -57 days float and every "
    "day of delay impacts our Jun 1 backfeed.\n\n"
    "We need today:\n"
    "1. Approval status — are the calcs accepted?\n"
    "2. If not, specific comments so Nello can respond same-day\n"
    "3. Committed date for final approval\n\n"
    "Regards"
)
pdf.ln(3)

# Owner/MPT email
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'TO: Owner — MPT Delivery Confirmation', 1, 1, 'L', True)
pdf.set_font('Helvetica', '', 8)
pdf.multi_cell(0, 4,
    "Subject: Blackford Solar — Main Power Transformer Delivery Status — Urgent\n\n"
    "Team,\n\n"
    "The MPT is the single largest item on the substation critical path (-65 days float). "
    "Our schedule shows delivery by Mar 6 but we have no confirmed ship date or tracking.\n\n"
    "We need immediately:\n"
    "1. Confirmed ship date and carrier/tracking information\n"
    "2. If not yet shipped — revised delivery date with explanation\n"
    "3. Acknowledgment of cost notice sent last week re: delivery delays\n\n"
    "Our crane and install crew are scheduled based on a Mar 6 arrival. If this slips, we need "
    "to know NOW to re-sequence.\n\n"
    "Regards"
)
pdf.ln(3)

# QI Staffing email
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'TO: Gabryal / Bill Nichols — QI Staffing (6 Needed)', 1, 1, 'L', True)
pdf.set_font('Helvetica', '', 8)
pdf.multi_cell(0, 4,
    "Subject: Blackford Solar — QI Staffing Plan Needed by Mar 3\n\n"
    "Gabryal / Bill,\n\n"
    "We need 6 QIs on a 38-block site with zero currently on site. 1 is confirmed for Mar 5. "
    "Electrical testing starts Mar 6. Production is ramping across 30+ blocks.\n\n"
    "By Monday Mar 3, please provide:\n"
    "1. Staffing plan with names and arrival dates for all 6 positions\n"
    "2. Current defect/remediation rate (actual numbers)\n"
    "3. Plan for qualified electrical QC before VOC testing starts Mar 6\n\n"
    "Using laborers for quality inspections is a temporary measure that cannot continue as "
    "production scales. This needs to be solved this week.\n\n"
    "Regards"
)

# Save
output_path = "/opt/goliath/projects/blackford/reports/2026-02-27-blackford-high-priority-probing.pdf"
pdf.output(output_path)
print(f"PDF saved to: {output_path}")
