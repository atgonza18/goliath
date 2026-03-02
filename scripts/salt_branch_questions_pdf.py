#!/usr/bin/env python3
"""Generate Salt Branch Pre-Call Constraint Questions PDF."""

from fpdf import FPDF
from datetime import date

OUTPUT_PATH = "/opt/goliath/projects/salt-branch/reports/2026-02-27-salt-branch-constraint-questions.pdf"

class SaltBranchPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 6, "GOLIATH | DSC Construction Operations", align="R", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Salt Branch Constraint Questions | {date.today().strftime('%B %d, %Y')} | Page {self.page_no()}/{{nb}}", align="C")

    def section_header(self, text, r=180, g=40, b=40):
        self.set_font("Helvetica", "B", 13)
        self.set_fill_color(r, g, b)
        self.set_text_color(255, 255, 255)
        self.cell(0, 9, f"  {text}", fill=True, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def constraint_block(self, title, status_lines, questions):
        # Check if we need a new page (estimate block height)
        needed = 20 + len(status_lines) * 6 + len(questions) * 18
        if self.get_y() + needed > 265:
            self.add_page()

        # Constraint title
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 30, 30)
        self.cell(0, 7, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

        # Status details
        self.set_font("Helvetica", "", 9)
        self.set_text_color(60, 60, 60)
        for line in status_lines:
            # Bold the label part before the colon
            if ":" in line:
                parts = line.split(":", 1)
                self.set_font("Helvetica", "B", 9)
                self.cell(self.get_string_width(parts[0] + ":") + 1, 5.5, parts[0] + ":")
                self.set_font("Helvetica", "", 9)
                self.cell(0, 5.5, parts[1].strip(), new_x="LMARGIN", new_y="NEXT")
            else:
                self.cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

        # Questions
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(180, 40, 40)
        self.cell(0, 5.5, "PROBING QUESTIONS:", new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

        for i, q in enumerate(questions, 1):
            if self.get_y() + 16 > 265:
                self.add_page()
            self.set_font("Helvetica", "B", 9)
            self.set_text_color(30, 30, 30)
            x_start = self.get_x()
            self.cell(7, 5, f"{i}.")
            self.set_font("Helvetica", "", 9)
            self.set_text_color(40, 40, 40)
            self.multi_cell(0, 5, q, new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

        # Separator
        self.set_draw_color(200, 200, 200)
        self.line(15, self.get_y(), 195, self.get_y())
        self.ln(4)


def build_pdf():
    pdf = SaltBranchPDF("P", "mm", "Letter")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 12, "SALT BRANCH", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 8, "Pre-Call Constraint Questions", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, f"Prepared: {date.today().strftime('%B %d, %Y')} | Data Source: DSC Report 2/20/2026 (7 days stale)", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # Executive snapshot
    pdf.set_fill_color(255, 245, 238)
    pdf.set_draw_color(200, 80, 40)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(180, 50, 20)
    y_start = pdf.get_y()
    pdf.rect(10, y_start, 190, 32, style="DF")
    pdf.set_xy(14, y_start + 3)
    pdf.cell(0, 5.5, "EXECUTIVE SNAPSHOT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(60, 40, 30)
    snapshot_lines = [
        "11 Open Constraints (up 2 from prior week) | 43 Closed",
        "2 BLOCKING: Safety hold + Remediation/Turnover => Racking & Modules at ZERO production",
        "Piles: 175/day vs 1,300 required (13%) | Racking: ON HOLD | Modules: ON HOLD",
        "Financial Exposure: $4M+ predrill risk + unquantified productivity + daily hold costs + LD risk",
    ]
    for line in snapshot_lines:
        pdf.set_x(14)
        pdf.cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    # ========== HIGH PRIORITY ==========
    pdf.section_header("HIGH PRIORITY CONSTRAINTS", 180, 40, 40)

    pdf.constraint_block(
        "1. SAFETY MANPOWER HOLD -- Racking & Modules Halted",
        [
            "Status: Racking and module installation completely halted. Zero production.",
            "Owner: Safety department (specific person UNNAMED)",
            "Need-by: IMMEDIATE -- every day = zero racking + zero module output",
            "Last Update: 'Coordinating with safety department' -- no committed date",
        ],
        [
            "It has been 7 days since this was reported. Give me the NAME of the specific person in the safety department who owns this staffing request, the EXACT number of safety personnel needed, and the DATE they will be on site. 'Coordinating' is not a status.",
            "What is the daily cost of this safety hold in terms of schedule slip and LD exposure? Has that number been escalated to the safety department's leadership to force prioritization?",
            "If the safety department cannot staff this within 48 hours, what is Plan B? Third-party safety staffing? Temporary reassignment from other DSC projects? Who is authorized to make that call, and why hasn't it been made already?",
        ],
    )

    pdf.constraint_block(
        "2. REMEDIATION & BLOCK TURNOVER -- Racking Cannot Start",
        [
            "Status: Evaluating additional manpower/tooling. No decisions made after 7 days.",
            "Owner: Site construction team / remediation lead (UNNAMED)",
            "Need-by: IMMEDIATE -- racking cannot begin in new blocks without turnover",
            "Impact: Even if safety clears, racking stays at zero without block turnover",
        ],
        [
            "'Evaluating' was the status 7 days ago. Has the evaluation been completed? What SPECIFIC additional crew count and tooling has been identified, ordered, or mobilized? If still evaluating after a week, who is accountable for that delay?",
            "How many blocks are currently awaiting turnover? At current remediation rates, when do we run out of available blocks for racking -- even after the safety hold lifts?",
            "What is the specific sequence and realistic timeline for each step to get racking back to 45/day? I need dates, not intentions.",
        ],
    )

    pdf.constraint_block(
        "3. PILE PRODUCTION COLLAPSE -- 175/day vs 1,300/day Required",
        [
            "Status: Rate dropped 65% (500/day to 175/day). 44% complete.",
            "Gap: 7.4x production increase needed to meet schedule",
            "Data Conflict: Internal tracking shows different numbers (1,400 req'd, 240->130 drop)",
            "Recovery Plan: None described in last report",
        ],
        [
            "What SPECIFICALLY caused the 65% drop from 500/day to 175/day? I need the root cause -- weather, equipment failure, crew demob, or geotech conditions -- with specifics on what happened and when.",
            "175/day vs 1,300/day means we need an 8x increase. How many rigs are on site vs. needed? How many crew members today vs. needed? When do additional resources arrive? If 1,300/day is unreachable, what IS the realistic peak rate and what does that do to the schedule?",
            "We have conflicting pile numbers internally vs. Josh's report. Which are correct? This gets resolved before we leave this call.",
        ],
    )

    pdf.constraint_block(
        "4. MODULE INSTALLATION -- 1,300/day vs 7,800/day Required + ON HOLD",
        [
            "Status: ON HOLD (safety). Even pre-hold rate was 17% of target.",
            "Completion: Only 3% complete. 6x production gap.",
            "Owner: Module installation crew",
            "Compounding: Safety hold + pre-existing production deficit",
        ],
        [
            "Even BEFORE the safety hold, we were at 17% of required rate. The safety hold is not the only problem. When the hold lifts, what SPECIFIC changes (crew additions, shifts, equipment) will close the gap?",
            "Is 7,800/day physically achievable on this site with available laydown, crane capacity, and crew density? If not, what is the HONEST maximum achievable rate, and what does that mean for the SC date?",
            "At 3% complete with a 6x gap and a safety hold, what is the realistic module completion date? What is the LD exposure at 2 months late? 3 months late? Give me the numbers.",
        ],
    )

    pdf.constraint_block(
        "5. RACKING PRODUCTION -- 18/day vs 45/day Required + ON HOLD",
        [
            "Status: ON HOLD (safety). Rate was already declining pre-hold (25->18/day). 22% complete.",
            "Double Gate: Cannot resume until safety hold cleared AND blocks turned over",
            "Owner: Racking crew / site construction manager",
        ],
        [
            "Racking was declining BEFORE the safety hold -- 25/day to 18/day. What was driving that? Block availability, crew issues, or material supply? When the hold lifts, we need to accelerate to 45/day, not resume at 18.",
            "Racking is gated by BOTH safety AND block turnover. Give me a single date when BOTH gates will be cleared and racking crews will be productive. Not two separate vague updates -- one date.",
            "Are racking crews still on site being paid during this hold? What is the daily burn rate for idle racking crews? If demobilized, what is the remob timeline and cost?",
        ],
    )

    # ========== MEDIUM PRIORITY ==========
    pdf.section_header("MEDIUM PRIORITY CONSTRAINTS", 200, 140, 40)

    pdf.constraint_block(
        "6. $4M+ COMMERCIAL RISK -- Predrill Activities",
        [
            "Status: EAC (2/7 actuals) shows $4M+ commercial risk on predrill",
            "Owner: Project controls / commercial team (UNNAMED)",
            "Need-by: Resolution path needed before next EAC update",
        ],
        [
            "What SPECIFICALLY about predrill is driving $4M+? Scope change, sub claim, productivity variance, or design issue? 'Related to predrill' is too vague for a $4M problem.",
            "Is any portion recoverable via change order to the owner? Has the CO been submitted? What is the owner's response timeline?",
            "The EAC was based on 2/7 actuals -- 20 days old. Given the production collapses and safety hold since then, is $4M still the right number or has it grown?",
        ],
    )

    pdf.constraint_block(
        "7. UNQUANTIFIED PRODUCTIVITY COST IMPACT",
        [
            "Status: 7.4x pile gap + 2.5x racking gap + 6x module gap + safety hold costs NOT quantified",
            "Owner: Project controls / cost engineering",
            "Risk: Likely growing daily due to ongoing holds and rate gaps",
        ],
        [
            "What is the TOTAL productivity cost impact of the 7.4x pile gap, 2.5x racking gap, and 6x module gap? This should be a number. If project controls hasn't run it, why not, and when will it be ready?",
            "What is the daily cost of the safety hold across all halted work fronts -- idle labor, equipment standby, schedule overhead? Multiply by days on hold. That is the minimum additional cost. Has it been captured?",
            "At what dollar amount does cumulative risk (predrill + productivity + hold costs + LDs) trigger an executive review or project re-baseline? What is that threshold and how close are we?",
        ],
    )

    pdf.constraint_block(
        "8. UG ELECTRICAL -- DC Completion Forecast June 2026",
        [
            "Status: 48% complete, tracking to June 2026 forecast",
            "Owner: Electrical crew lead",
            "Note: Only work front not in crisis",
        ],
        [
            "UG Electrical appears on track. Is the June 2026 forecast still valid given disruptions on other fronts, or are there shared resources (crews, access, equipment) that could pull electrical off track?",
            "Is there any risk that electrical personnel get pulled for remediation or recovery efforts, jeopardizing the June completion?",
            "If piles, racking, and modules all slip significantly, does June electrical completion even matter? What is the actual critical path right now?",
        ],
    )

    # ========== DATA ISSUE ==========
    pdf.section_header("OPEN ISSUE: DATA DISCREPANCY", 80, 80, 80)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(50, 50, 50)
    pdf.multi_cell(0, 5.5, "Internal tracking shows piles at 1,400/day required with a drop from 240 to 130/day. Josh Hauger's 2/20 report shows 1,300/day required with a drop from 500 to 175/day. These are materially different numbers that must be reconciled.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(180, 40, 40)
    pdf.cell(0, 5.5, "QUESTIONS:", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.cell(7, 5, "1.")
    pdf.multi_cell(0, 5, "Which data source is correct -- our internal tracking or Josh's weekly report? Are we looking at different time periods, scopes, or is someone's data wrong?", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)
    pdf.set_x(pdf.get_x())
    pdf.cell(7, 5, "2.")
    pdf.multi_cell(0, 5, "If both are 'correct' but measuring different things, explain exactly what each represents so everyone on this call works from the same baseline going forward.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    # Call theme box
    pdf.set_fill_color(240, 240, 250)
    pdf.set_draw_color(60, 60, 120)
    y_start = pdf.get_y()
    box_h = 28
    if y_start + box_h > 260:
        pdf.add_page()
        y_start = pdf.get_y()
    pdf.rect(10, y_start, 190, box_h, style="DF")
    pdf.set_xy(14, y_start + 3)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(40, 40, 100)
    pdf.cell(0, 6, "CALL THEME: DEMAND SPECIFICS", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(14)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(50, 50, 80)
    pdf.multi_cell(182, 5, "Two constraints are zeroing out production on two of four major work fronts. A 7.4x gap on the third. Only electrical is tracking. We are 7 days past the last update with no committed resolution dates. Every answer on this call needs a SPECIFIC NAME, a SPECIFIC DATE, and a SPECIFIC NUMBER. 'Coordinating' and 'evaluating' are not acceptable status updates.", new_x="LMARGIN", new_y="NEXT")

    pdf.output(OUTPUT_PATH)
    print(f"PDF saved to {OUTPUT_PATH}")

if __name__ == "__main__":
    build_pdf()
