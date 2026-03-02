#!/usr/bin/env python3
"""Generate Blackford Solar probing questions PDF."""
from fpdf import FPDF
import os

class ProbingQuestionsPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, 'BLACKFORD SOLAR - PROBING QUESTIONS FOR CALL PREP', 0, 1, 'C')
        self.cell(0, 5, 'February 27, 2026 | Prepared by Goliath / DSC', 0, 1, 'C')
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f'Blackford Solar - Probing Questions | Page {self.page_no()}/{{nb}}', 0, 0, 'C')

    def section_header(self, title, color=(0, 0, 0)):
        self.set_font('Helvetica', 'B', 13)
        self.set_text_color(*color)
        self.cell(0, 8, title, 0, 1)
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def constraint_block(self, number, title, priority, status, owner, due_date, discipline, latest_note, questions, schedule_context=None):
        # Constraint header bar
        if priority == "HIGH":
            r, g, b = 180, 30, 30
            badge = "HIGH"
        elif priority == "MEDIUM":
            r, g, b = 200, 140, 0
            badge = "MEDIUM"
        else:
            r, g, b = 80, 80, 80
            badge = "LOW"

        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.set_font('Helvetica', 'B', 11)
        self.cell(0, 8, f'  {number}. {title}', 1, 1, 'L', True)

        self.set_text_color(0, 0, 0)
        self.set_font('Helvetica', '', 9)
        self.ln(1)

        # Metadata row
        self.set_font('Helvetica', 'B', 9)
        self.cell(20, 5, 'Owner:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(45, 5, owner, 0, 0)
        self.set_font('Helvetica', 'B', 9)
        self.cell(15, 5, 'Due:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(35, 5, due_date, 0, 0)
        self.set_font('Helvetica', 'B', 9)
        self.cell(22, 5, 'Status:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(30, 5, status, 0, 0)
        self.set_font('Helvetica', 'B', 9)
        self.cell(22, 5, 'Discipline:', 0, 0)
        self.set_font('Helvetica', '', 9)
        self.cell(0, 5, discipline, 0, 1)
        self.ln(1)

        # Latest update
        self.set_font('Helvetica', 'B', 9)
        self.cell(0, 5, 'Latest Update:', 0, 1)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(60, 60, 60)
        self.multi_cell(0, 4, latest_note)
        self.set_text_color(0, 0, 0)
        self.ln(1)

        # Schedule cross-reference if available
        if schedule_context:
            self.set_font('Helvetica', 'B', 9)
            self.set_text_color(180, 30, 30)
            self.cell(0, 5, 'Schedule Impact:', 0, 1)
            self.set_font('Helvetica', '', 8)
            self.multi_cell(0, 4, schedule_context)
            self.set_text_color(0, 0, 0)
            self.ln(1)

        # Questions
        self.set_font('Helvetica', 'B', 9)
        self.cell(0, 5, 'Probing Questions:', 0, 1)
        self.set_font('Helvetica', '', 9)
        for i, q in enumerate(questions, 1):
            self.set_font('Helvetica', 'B', 9)
            self.cell(8, 5, f'Q{i}:', 0, 0)
            self.set_font('Helvetica', '', 9)
            self.multi_cell(0, 5, q)
            self.ln(1)

        self.ln(3)


pdf = ProbingQuestionsPDF()
pdf.alias_nb_pages()
pdf.add_page()

# Executive Snapshot
pdf.set_font('Helvetica', 'B', 14)
pdf.set_text_color(0, 0, 0)
pdf.cell(0, 8, 'EXECUTIVE SNAPSHOT', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Blackford Solar (211 MW DC, 38 blocks) is running -53 days float on the critical path. "
    "The schedule data date is Feb 23, 2026. There are 8 active constraints (3 HIGH, 4 MEDIUM, 1 LOW). "
    "5 constraints are overdue on their need-by dates. Key risks: ground conditions halting civil work, "
    "Timmons PPP turnaround delays stalling pile production, T-line engineering approvals dragging from Nello/PVI, "
    "and QI staffing gaps threatening quality control as production ramps up. "
    "First Circuit Mechanical Completion (originally Nov 20, 2025) is now projected Mar 25, 2026 -- 79 days behind. "
    "Backfeed/Interconnect and HV Works are at -94 days float. The substation is at -94 days float with "
    "MPT delivery still pending. COD (Jun 5, 2026) is showing -53 days of negative float."
)
pdf.ln(5)

# ============ HIGH PRIORITY ============
pdf.section_header('HIGH PRIORITY CONSTRAINTS', (180, 30, 30))

pdf.constraint_block(
    1,
    "PV Grading Delays",
    "HIGH", "IN PROGRESS (OVERDUE)", "Ross", "Nov 17, 2025", "Civil Engineering",
    "2/25: Ground conditions are still not suitable to get these blocks installed with piles. 5 blocks left. 2 week catchup time to these blocks with full working hours.",
    [
        "Ross, you've had 5 blocks remaining since early February and ground conditions have been the reason every single week. What is the specific threshold (soil moisture %, temperature, etc.) that needs to be met before grading can resume -- and what is the weather forecast showing for that window?",
        "You mentioned '2 week catchup time with full working hours' -- what equipment and crew size does that require, and is that equipment currently on site or does it need to be mobilized? Have you confirmed availability?",
        "Blocks 21, 22, 25, and 33 have been stuck since December. At what point do we need to escalate to a different approach (lime stabilization, geo-grid, etc.) instead of waiting for Mother Nature? Has a cost analysis been done on alternative ground improvement methods vs. the daily delay cost?",
    ],
    schedule_context="Pile foundations are at -9.5 to -36.5 days float. Every day grading is delayed pushes piles further behind. Array construction at -38.5 days float depends on piles being complete. This is feeding the -53 day overall project float."
)

pdf.constraint_block(
    2,
    "PPP Turn Over Issue",
    "HIGH", "IN PROGRESS (OVERDUE)", "Scott M.", "Jan 19, 2026", "Piles",
    "2/25: No response yet from Timmons. Reaching out to Ferdinand. Jacqueline followed up this morning. Block 29 opening up for piles (3 blocks total).",
    [
        "Timmons has had the surface files since Friday Feb 20 -- that's 5 business days with no response. What is the contractual turnaround time for PPP reviews, and have we formally notified them they're in breach of that timeline?",
        "You said 'at current production rate, piles will run out of work' on 2/16. We're now 11 days past that warning. How many days of pile work remain in the currently approved blocks, and what's the exact date piles go idle if Timmons doesn't deliver?",
        "Surface file issues were flagged for Block 20 north section, Block 10, and Block 12. If micro-grading is needed, how long does that add to each block and who is responsible for that scope -- us or the civil sub?",
    ],
    schedule_context="Pile shakeout ends Mar 23, pile foundations end Mar 31 per schedule. PPP delays directly gate pile installation. With -9.5 days float on the last pile block, there is ZERO room for further Timmons delays."
)

pdf.add_page()

pdf.constraint_block(
    3,
    "T-Line Pole Configuration Conflict",
    "HIGH", "OPEN (OVERDUE)", "Nick", "Feb 17, 2026", "Logistics/Engineering",
    "2/25: Approval from everyone that the calcs work. Calcs got sent to PVI, PVI is currently going through them, should get a clean response and green light from PVI on Friday. Hoping for Keeley return on Wednesday.",
    [
        "PVI has had the calcs -- when exactly did they receive them, and what is the committed response date? If Friday passes without PVI approval, what is the escalation plan and who at PVI do we call?",
        "Nello dragged their feet for over 2 weeks on these calcs. The poles are physically on site but we can't install them without engineering sign-off. What is the daily cost of having poles sitting on site with an idle T-line crew?",
        "The T-line schedule shows -57 days float with erection, stringing, and commissioning all remaining. Once PVI gives the green light, what is the realistic timeline to complete T-line vs. the Jun 1 backfeed date? Is a recovery schedule in place?",
    ],
    schedule_context="T-Line at -57 days float. Foundations (11 days remaining), Erection (15 days), String/Sag/Clip (15 days), and Commissioning (7 days) all still outstanding. Backfeed target is Jun 1 which is already at -94 days float."
)

# ============ MEDIUM PRIORITY ============
pdf.section_header('MEDIUM PRIORITY CONSTRAINTS', (200, 140, 0))

pdf.constraint_block(
    4,
    "Backfill Delays Due to Weather/Site Conditions",
    "MEDIUM", "IN PROGRESS (OVERDUE)", "Ross", "Feb 15, 2026", "UG Electrical",
    "2/25: Block 29 is done, backfilling 20. Block 30 should be closed up (minus underground transitions), mobilized to Block 10.",
    [
        "Sand backfill was approved and seems to be working. What is the daily production rate now vs. before the sand solution, and how many blocks per week can you close out at this new rate?",
        "You're in Block 20 and mobilizing to Block 10 -- what's the full remaining sequence and how many blocks still need backfill? Can you give me a date when all underground backfill will be complete?",
        "The schedule shows DC wiring at -5 days float on the last blocks. Are we pacing backfill to stay ahead of the racking/module crews, or is there a risk that backfill becomes a bottleneck again when production ramps?",
    ],
    schedule_context="UG DC Wiring at -5 to -42.5 days float. AC Collection at -24 to -24.5 days float. Backfill completion gates the entire electrical subgrade sequence."
)

pdf.constraint_block(
    5,
    "IT Hardware/Setup for HeavyJobs & Smart TagIT",
    "MEDIUM", "OPEN (OVERDUE)", "DSC", "Feb 10, 2026", "Logistics",
    "2/25: Rebecca will work with Scott to get foreman signed up and tickets submitted.",
    [
        "How many new foremen are coming on board, and what's the timeline? If IT tickets are taking 'significant time' as noted on 2/4, what's the average turnaround time and can we pre-stage hardware before they arrive?",
        "Ineight training is happening -- is every foreman who needs access actually trained and set up, or are some falling through the cracks? What's the tracking mechanism?",
        "Smart TagIT is addressed per notes -- is it fully functional and being used in the field for pile tracking? If not, how are we tracking pile installation quality without it?",
    ]
)

pdf.add_page()

pdf.constraint_block(
    6,
    "QIs Needed (6)",
    "MEDIUM", "OPEN (due Mar 1)", "Gabryal", "Mar 1, 2026", "Quality",
    "2/25: 1 QI coming in on the 5th. Bridging the gap utilizing laborers to reduce remediation issues to minimize observations. FE's will be out in the field more to bridge the gap as well. Will follow up with Bill Nichols to get him some heads.",
    [
        "You need 6 QIs and only 1 is confirmed for March 5. What is the plan to fill the other 5 positions, and what is the realistic timeline? Have requisitions been posted?",
        "Using laborers to 'bridge the gap' on quality inspections is a stopgap, not a solution. What is the defect/remediation rate right now, and has it increased since we've been short on QIs? I want the actual numbers.",
        "As production ramps up with 38 blocks in various stages of construction, the QI shortage will get worse, not better. At peak production, how many QIs are actually needed vs. what we'll have? Is there a staffing plan tied to the production curve?",
    ],
    schedule_context="Pile remediation runs through Apr 15. Array construction through May 28. Without adequate QIs, rework and remediation will increase, adding delays to an already -53 day float schedule."
)

pdf.constraint_block(
    7,
    "MPT Delivery Date",
    "MEDIUM", "OPEN (due Mar 3)", "Ross", "Mar 3, 2026", "Procurement",
    "2/25: Notice being drafted to get cost accounted for on delivery delays. Notice sent last week. Follow up notices pending.",
    [
        "The schedule shows MPT install starting Mar 9 with -65 days float. Is the MPT physically on its way? What is the confirmed delivery date and has the shipping company provided tracking?",
        "You mentioned a notice was sent for cost accounting on delivery delays -- who is the notice addressed to (owner? vendor?) and what is the estimated cost impact of each day of MPT delay?",
        "Once the MPT arrives, the install sequence is 5 days for placement + 5 days for assembly and oil fill. Is the crew and crane already scheduled, or do we need to coordinate that? Any risk the crew won't be available when the MPT arrives?",
    ],
    schedule_context="MPT install at -65 days float. Substation equipment install/bus work at -88 days float. Substation testing & commissioning can't start until May 1 and runs through Jun 1 for energization. The substation is on the critical path to backfeed (Jun 1, -94 days float)."
)

# ============ LOW PRIORITY ============
pdf.section_header('LOW PRIORITY CONSTRAINTS', (80, 80, 80))

pdf.constraint_block(
    8,
    "Material Storage Blocks (25, 21, 22)",
    "LOW", "IN PROGRESS (OVERDUE)", "Rebecca M.", "Nov 17, 2025", "Logistics",
    "2/23: Block 22 is cleared, 21 not clearing modules because torque tubes are being moved from 25. Track loader at Delta Bobcat needs to get shipped to Blackford.",
    [
        "Block 22 is cleared -- great. Block 21 is waiting on torque tubes to move from 25. When exactly will that track loader arrive from Delta Bobcat, and who is coordinating that transfer?",
        "Block 25 has piles and torque tubes for the whole project and 'might be problematic for a few weeks.' These are construction materials needed across the site -- is there a staged drawdown plan so Block 25 can be cleared incrementally as materials are consumed?",
        "This constraint has been open since November 2025 -- over 3 months. At what point does the material storage issue require a more aggressive solution like off-site staging to free up these blocks for construction?",
    ],
    schedule_context="Blocks 21, 22, 25 are scheduled for pile foundations and array construction. Every day these blocks are occupied by material storage is a day they can't enter the construction sequence."
)

# ============ CALL THEME ============
pdf.add_page()
pdf.section_header('CALL THEME & STRATEGY', (0, 0, 120))
pdf.set_font('Helvetica', '', 10)
pdf.multi_cell(0, 5,
    "This project is -53 days behind schedule with every major milestone showing negative float. "
    "The team has been dealing with persistent weather and ground condition issues since December, "
    "but the pattern of 'no update / still waiting / conditions not allowing' responses needs to shift "
    "to proactive recovery planning.\n\n"
    "KEY DEMANDS FOR THIS CALL:\n\n"
    "1. RECOVERY SCHEDULE -- Ask for a resource-loaded recovery schedule that shows how the team "
    "plans to recover the -53 days of float. Vague 'we'll catch up' is not acceptable. Need dates, "
    "crew counts, equipment lists, and overtime plans.\n\n"
    "2. THIRD-PARTY ACCOUNTABILITY -- Timmons (PPP), Nello (T-line calcs), PVI (T-line approval), "
    "and the MPT vendor all have deliverables that are late or at risk. For each one: what is the "
    "committed date, who is the escalation contact, and what happens if they miss it?\n\n"
    "3. QI STAFFING PLAN -- 6 QIs needed, 1 confirmed. As production ramps, quality will suffer. "
    "Need a concrete staffing plan tied to the production curve, not hope.\n\n"
    "4. COST EXPOSURE -- With -53 to -94 days of negative float, LDs and extended general conditions "
    "are a real risk. Has anyone modeled the total cost exposure? If not, that needs to happen this week.\n\n"
    "5. DAILY/WEEKLY CADENCE -- The notes show multi-day gaps between updates on critical constraints. "
    "Establish a daily constraint review cadence for HIGH priority items."
)

pdf.ln(5)
pdf.section_header('FOLLOW-UP EMAIL DRAFTS', (0, 0, 120))
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'To: Ferdinand / Timmons (PPP Turnaround)', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Subject: URGENT - Blackford Solar PPP Review Status\n\n"
    "Ferdinand,\n\n"
    "Surface files were submitted to Timmons on Friday 2/20. As of today (2/27), we have not received "
    "the reviewed PPP packages. Our pile crews are at risk of running out of approved work blocks, "
    "which will result in idle equipment and direct schedule impact to an already critical project.\n\n"
    "Can you confirm: (1) When will the PPP packages be returned? (2) Are there any issues with the "
    "surface files for Blocks 10, 12, and 20 north section? (3) If micro-grading is needed, we need "
    "that flagged immediately so we can plan accordingly.\n\n"
    "Please respond by EOD today. This is the #1 constraint impacting pile production.\n\n"
    "Thank you."
)

pdf.ln(3)
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'To: PVI (T-Line Pole Configuration)', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Subject: Blackford Solar - T-Line Pole Calcs Review\n\n"
    "Team,\n\n"
    "Nello has submitted the pole configuration calculations for your review. All poles are physically "
    "on site and the T-line crew is standing by. The T-line schedule is currently at -57 days float "
    "and every day of delay directly impacts our Jun 1 backfeed date.\n\n"
    "Can you confirm: (1) Expected review completion date? (2) Are there any preliminary concerns "
    "with the calculations? (3) If additional information is needed from Nello, please communicate "
    "that immediately so we can expedite.\n\n"
    "We are targeting a green light by Friday 2/27. Please advise if that is achievable.\n\n"
    "Thank you."
)

pdf.ln(3)
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'To: Gabryal / Bill Nichols (QI Staffing)', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Subject: Blackford Solar - QI Staffing Gap (6 Needed)\n\n"
    "Gabryal / Bill,\n\n"
    "We currently need 6 QIs for Blackford Solar with only 1 confirmed for March 5. With 38 blocks "
    "in various stages of pile, racking, and module installation, quality oversight is critical to "
    "avoid costly rework.\n\n"
    "Can you provide: (1) A staffing plan with target dates for filling all 6 positions? "
    "(2) Current defect/remediation rate to quantify the impact of the QI gap? "
    "(3) Any interim solutions beyond using laborers and FE field presence?\n\n"
    "This needs to be addressed before production ramps further.\n\n"
    "Thank you."
)

pdf.ln(3)
pdf.set_font('Helvetica', 'B', 10)
pdf.cell(0, 6, 'To: Ross (MPT Delivery + PV Grading)', 0, 1)
pdf.set_font('Helvetica', '', 9)
pdf.multi_cell(0, 5,
    "Subject: Blackford Solar - MPT Delivery Confirmation + Grading Recovery Plan\n\n"
    "Ross,\n\n"
    "Two items:\n\n"
    "1. MPT DELIVERY: The schedule shows MPT install starting Mar 9. Please confirm the exact delivery "
    "date and provide shipping tracking if available. The substation path is at -94 days float and we "
    "cannot afford further delays.\n\n"
    "2. PV GRADING: 5 blocks remain (33, 21, 22, 25, and partial others). Ground conditions have been "
    "the cited reason since December. Please provide: (a) a specific grading recovery schedule with "
    "target dates per block, (b) contingency plan if ground conditions don't improve in the next 2 weeks, "
    "and (c) cost estimate for alternative ground improvement methods.\n\n"
    "Please respond with both items by EOD Friday.\n\n"
    "Thank you."
)

# Save
output_path = "/opt/goliath/projects/blackford/reports/2026-02-27-blackford-probing-questions.pdf"
pdf.output(output_path)
print(f"PDF saved to: {output_path}")
