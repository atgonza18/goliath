#!/usr/bin/env python3
"""
Generate the Pecan Prairie APM Questions PDF report.
Uses reportlab for professional PDF generation with TOC, page numbers,
bold category headers, and highlighted urgent summary section.
"""

import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle,
    KeepTogether, HRFlowable, NextPageTemplate, PageTemplate, Frame,
    BaseDocTemplate
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.fonts import tt2ps
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ── Colors ──────────────────────────────────────────────────────────────
DARK_BLUE = HexColor("#1B3A5C")
MEDIUM_BLUE = HexColor("#2E6B9E")
LIGHT_BLUE = HexColor("#E8F0FE")
ACCENT_RED = HexColor("#C0392B")
ACCENT_ORANGE = HexColor("#E67E22")
LIGHT_RED = HexColor("#FDEDEC")
LIGHT_ORANGE = HexColor("#FEF5E7")
DARK_GRAY = HexColor("#2C3E50")
MEDIUM_GRAY = HexColor("#7F8C8D")
LIGHT_GRAY = HexColor("#F4F6F7")
BORDER_GRAY = HexColor("#BDC3C7")
WHITE = white

OUTPUT_DIR = "/workspaces/goliath/dsc-constraints-production-reports/2026-02-25"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "2026-02-25_Pecan-Prairie_APM-Questions.pdf")


class NumberedDocTemplate(BaseDocTemplate):
    """Custom doc template with page numbers and running headers."""

    def __init__(self, filename, **kwargs):
        BaseDocTemplate.__init__(self, filename, **kwargs)
        self.page_count = 0
        self.toc_entries = []

    def afterFlowable(self, flowable):
        """Track headings for TOC."""
        if hasattr(flowable, '_bookmarkName'):
            level = flowable._bookmarkLevel
            text = flowable._bookmarkText
            page = self.page
            self.toc_entries.append((level, text, page))

    def afterPage(self):
        self.page_count += 1


def header_footer(canvas, doc):
    """Draw header and footer on each page."""
    canvas.saveState()
    width, height = letter

    # Header line
    canvas.setStrokeColor(DARK_BLUE)
    canvas.setLineWidth(1.5)
    canvas.line(0.75 * inch, height - 0.6 * inch, width - 0.75 * inch, height - 0.6 * inch)

    # Header text
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(DARK_BLUE)
    canvas.drawString(0.75 * inch, height - 0.55 * inch, "PECAN PRAIRIE SOLAR")
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MEDIUM_GRAY)
    canvas.drawRightString(width - 0.75 * inch, height - 0.55 * inch,
                           "DSC Operations Lead Question Bank  |  February 25, 2026")

    # Footer line
    canvas.setStrokeColor(BORDER_GRAY)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.6 * inch, width - 0.75 * inch, 0.6 * inch)

    # Footer text
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MEDIUM_GRAY)
    canvas.drawString(0.75 * inch, 0.45 * inch, "CONFIDENTIAL  —  Prepared for DSC Operations Review")
    canvas.drawRightString(width - 0.75 * inch, 0.45 * inch, f"Page {doc.page}")

    canvas.restoreState()


def first_page_header_footer(canvas, doc):
    """Draw footer only on the first (cover) page."""
    canvas.saveState()
    width, height = letter
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MEDIUM_GRAY)
    canvas.drawString(0.75 * inch, 0.45 * inch, "CONFIDENTIAL  —  Prepared for DSC Operations Review")
    canvas.drawRightString(width - 0.75 * inch, 0.45 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build_styles():
    """Create all paragraph styles."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='CoverTitle',
        fontName='Helvetica-Bold',
        fontSize=26,
        leading=32,
        textColor=DARK_BLUE,
        alignment=TA_CENTER,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='CoverSubtitle',
        fontName='Helvetica',
        fontSize=14,
        leading=18,
        textColor=MEDIUM_BLUE,
        alignment=TA_CENTER,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='CoverDate',
        fontName='Helvetica',
        fontSize=12,
        leading=16,
        textColor=MEDIUM_GRAY,
        alignment=TA_CENTER,
        spaceAfter=20,
    ))
    styles.add(ParagraphStyle(
        name='ListHeader',
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=DARK_BLUE,
        spaceBefore=16,
        spaceAfter=8,
        borderWidth=0,
        borderPadding=0,
    ))
    styles.add(ParagraphStyle(
        name='CategoryHeader',
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        textColor=white,
        spaceBefore=14,
        spaceAfter=6,
        backColor=DARK_BLUE,
        borderPadding=(6, 8, 6, 8),
    ))
    styles.add(ParagraphStyle(
        name='QuestionNumber',
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13,
        textColor=DARK_BLUE,
        spaceBefore=8,
        spaceAfter=1,
    ))
    styles.add(ParagraphStyle(
        name='QuestionBody',
        fontName='Helvetica',
        fontSize=9,
        leading=12.5,
        textColor=DARK_GRAY,
        spaceBefore=0,
        spaceAfter=1,
        alignment=TA_JUSTIFY,
        leftIndent=14,
    ))
    styles.add(ParagraphStyle(
        name='WhyText',
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        leading=11.5,
        textColor=ACCENT_RED,
        spaceBefore=1,
        spaceAfter=6,
        leftIndent=14,
    ))
    styles.add(ParagraphStyle(
        name='UrgentSectionHeader',
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=ACCENT_RED,
        spaceBefore=16,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name='UrgentSubHeader',
        fontName='Helvetica-Bold',
        fontSize=10.5,
        leading=14,
        textColor=ACCENT_RED,
        spaceBefore=10,
        spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name='UrgentItem',
        fontName='Helvetica',
        fontSize=9,
        leading=12.5,
        textColor=DARK_GRAY,
        spaceBefore=1,
        spaceAfter=1,
        leftIndent=20,
        bulletIndent=10,
    ))
    styles.add(ParagraphStyle(
        name='TOCHeading',
        fontName='Helvetica-Bold',
        fontSize=16,
        leading=20,
        textColor=DARK_BLUE,
        spaceBefore=10,
        spaceAfter=20,
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        name='TOCEntry1',
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=18,
        textColor=DARK_BLUE,
        leftIndent=20,
    ))
    styles.add(ParagraphStyle(
        name='TOCEntry2',
        fontName='Helvetica',
        fontSize=9,
        leading=16,
        textColor=DARK_GRAY,
        leftIndent=40,
    ))
    styles.add(ParagraphStyle(
        name='SubCategoryHeader',
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=MEDIUM_BLUE,
        spaceBefore=10,
        spaceAfter=4,
    ))

    return styles


def add_cover_page(story, styles):
    """Build the cover page."""
    story.append(Spacer(1, 1.5 * inch))

    # Title block with colored background
    cover_data = [[""]]
    cover_table = Table(cover_data, colWidths=[6.5 * inch], rowHeights=[2.8 * inch])
    cover_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_BLUE),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 20),
        ('RIGHTPADDING', (0, 0), (-1, -1), 20),
        ('TOPPADDING', (0, 0), (-1, -1), 20),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 20),
        ('ROUNDEDCORNERS', (0, 0), (-1, -1), [8, 8, 8, 8]),
    ]))

    # Build inner content for cover
    inner_story = []
    inner_story.append(Paragraph(
        "PECAN PRAIRIE SOLAR",
        ParagraphStyle('ct1', fontName='Helvetica-Bold', fontSize=28, leading=34,
                       textColor=WHITE, alignment=TA_CENTER, spaceAfter=4)
    ))
    inner_story.append(Paragraph(
        "DSC Operations Lead Question Bank",
        ParagraphStyle('ct2', fontName='Helvetica', fontSize=16, leading=20,
                       textColor=HexColor("#A8C6E0"), alignment=TA_CENTER, spaceAfter=10)
    ))
    inner_story.append(HRFlowable(width="60%", thickness=1, color=HexColor("#A8C6E0"),
                                   spaceAfter=10, spaceBefore=4, dash=[2, 2]))
    inner_story.append(Paragraph(
        "North (407 MWdc) &amp; South (188 MWdc)",
        ParagraphStyle('ct3', fontName='Helvetica-Bold', fontSize=13, leading=17,
                       textColor=HexColor("#E8F0FE"), alignment=TA_CENTER, spaceAfter=6)
    ))
    inner_story.append(Paragraph(
        "Report Date: February 25, 2026",
        ParagraphStyle('ct4', fontName='Helvetica', fontSize=12, leading=16,
                       textColor=HexColor("#A8C6E0"), alignment=TA_CENTER)
    ))

    # Use a nested table approach for the cover box
    story.append(Spacer(1, 1.5 * inch))

    # Title
    story.append(Paragraph("PECAN PRAIRIE SOLAR", styles['CoverTitle']))
    story.append(Spacer(1, 4))
    story.append(Paragraph("DSC Operations Lead Question Bank", styles['CoverSubtitle']))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="50%", thickness=1.5, color=MEDIUM_BLUE,
                             spaceAfter=8, spaceBefore=4))
    story.append(Paragraph("North (407 MWdc) &amp; South (188 MWdc)", ParagraphStyle(
        'cs2', fontName='Helvetica-Bold', fontSize=13, leading=17,
        textColor=DARK_BLUE, alignment=TA_CENTER, spaceAfter=6)))
    story.append(Paragraph("Report Date: February 25, 2026", styles['CoverDate']))

    story.append(Spacer(1, 0.6 * inch))

    # Summary stats box
    stats_data = [
        [Paragraph("<b>93</b>", ParagraphStyle('s1', fontSize=22, alignment=TA_CENTER,
                                                textColor=DARK_BLUE, fontName='Helvetica-Bold')),
         Paragraph("<b>68</b>", ParagraphStyle('s2', fontSize=22, alignment=TA_CENTER,
                                                textColor=ACCENT_RED, fontName='Helvetica-Bold')),
         Paragraph("<b>25</b>", ParagraphStyle('s3', fontSize=22, alignment=TA_CENTER,
                                                textColor=MEDIUM_BLUE, fontName='Helvetica-Bold')),
         Paragraph("<b>6</b>", ParagraphStyle('s4', fontSize=22, alignment=TA_CENTER,
                                               textColor=ACCENT_ORANGE, fontName='Helvetica-Bold'))],
        [Paragraph("Total Questions", ParagraphStyle('l1', fontSize=8, alignment=TA_CENTER,
                                                      textColor=MEDIUM_GRAY)),
         Paragraph("Critical Constraints", ParagraphStyle('l2', fontSize=8, alignment=TA_CENTER,
                                                           textColor=MEDIUM_GRAY)),
         Paragraph("Production Follow-ups", ParagraphStyle('l3', fontSize=8, alignment=TA_CENTER,
                                                            textColor=MEDIUM_GRAY)),
         Paragraph("Categories", ParagraphStyle('l4', fontSize=8, alignment=TA_CENTER,
                                                  textColor=MEDIUM_GRAY))],
    ]
    stats_table = Table(stats_data, colWidths=[1.5 * inch] * 4, rowHeights=[0.45 * inch, 0.25 * inch])
    stats_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BLUE),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER_GRAY),
        ('LINEAFTER', (0, 0), (2, -1), 0.5, BORDER_GRAY),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
    ]))
    story.append(stats_table)

    story.append(PageBreak())


def add_toc_page(story, styles):
    """Build a manual table of contents page."""
    story.append(Paragraph("TABLE OF CONTENTS", styles['TOCHeading']))
    story.append(Spacer(1, 10))

    toc_items = [
        (1, "LIST 1: CRITICAL CONSTRAINT QUESTIONS (68 Questions)"),
        (2, "Category A: ERCOT Regulatory / Interconnection (Q1\u2013Q6)"),
        (2, "Category B: Permits & Regulatory, Non-ERCOT (Q7\u2013Q14)"),
        (2, "Category C: Engineering / IFC Packages (Q15\u2013Q29)"),
        (2, "Category D: Procurement / Material Delivery (Q30\u2013Q48)"),
        (2, "Category E: Subcontractor Mobilization & Field (Q49\u2013Q63)"),
        (2, "Category F: Owner / Commercial (Q64\u2013Q68)"),
        (1, "LIST 2: PRODUCTION ANALYSIS FOLLOW-UPS (25 Questions)"),
        (2, "A. Current Early-Phase Production (Q1\u2013Q3)"),
        (2, "B. Production Ramp Planning (Q4\u2013Q8)"),
        (2, "C. Equipment & Resources (Q9\u2013Q12)"),
        (2, "D. Weather & Ground Conditions (Q13\u2013Q15)"),
        (2, "E. Quality / Rework (Q16\u2013Q20)"),
        (2, "F. Safety & Labor (Q21\u2013Q25)"),
        (1, "URGENT SUMMARY \u2014 NEED ANSWERS BY END OF WEEK"),
    ]

    for level, text in toc_items:
        style_name = 'TOCEntry1' if level == 1 else 'TOCEntry2'
        prefix = "\u25A0  " if level == 1 else "\u25B8  "
        story.append(Paragraph(f"{prefix}{text}", styles[style_name]))

    story.append(PageBreak())


def make_question(num, title, body, why=None, styles=None):
    """Create a formatted question block."""
    elements = []

    # Determine if overdue/behind for color coding
    title_upper = title.upper() if title else ""
    if "OVERDUE" in title_upper:
        tag_color = ACCENT_RED
        tag_text = "OVERDUE"
    elif "BEHIND" in title_upper:
        tag_color = ACCENT_ORANGE
        tag_text = "BEHIND"
    else:
        tag_color = None
        tag_text = None

    # Question number + title
    if tag_color:
        q_title = f'<b>{num}.</b>  {title}'
    else:
        q_title = f'<b>{num}.</b>  {title}'

    elements.append(Paragraph(q_title, styles['QuestionNumber']))

    # Body
    if body:
        elements.append(Paragraph(body, styles['QuestionBody']))

    # WHY line
    if why:
        elements.append(Paragraph(f"<b>WHY:</b> {why}", styles['WhyText']))

    return KeepTogether(elements)


def add_list1(story, styles):
    """Add List 1: Critical Constraint Questions."""
    story.append(Paragraph(
        "LIST 1: CRITICAL CONSTRAINT QUESTIONS (68 Questions)",
        styles['ListHeader']
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=DARK_BLUE, spaceAfter=6))

    # ── CATEGORY A ──
    story.append(Paragraph(
        "CATEGORY A: ERCOT REGULATORY / INTERCONNECTION (HIGHEST PRIORITY)",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "1", "MS-1060 \u2014 Modeling Data Submission/Validation (North) \u2014 <font color='#C0392B'>OVERDUE</font> (due Feb 18)",
        "This activity was due 7 days ago and shows 0% complete. Has the modeling data package been submitted to ERCOT? If not, what specifically is blocking it? Provide the exact date the submission will be made.",
        "Prerequisite for QSA. If not submitted soon, QSA window (Apr 16) is at risk, pushing 1st injection (Jan 8, 2027).",
        styles=styles
    ))

    story.append(make_question(
        "2", "MS-1070 \u2014 Full Interconnection Study (FIS/TSP) (North) \u2014 <font color='#C0392B'>OVERDUE</font> (due Feb 23)",
        "FIS was due 2 days ago at 0%. Has the TSP completed the study? What is the hold? When will this be resolved?",
        "FIS must be complete 45 days prior to QSA. Every day past Feb 23 eats the buffer.",
        styles=styles
    ))

    story.append(make_question(
        "3", "MS-1080 \u2014 Reactive Power Study (North) \u2014 Due Feb 28 (3 days, 0%)",
        "Due in 3 days with zero progress. Who is performing this? Has the study been initiated?",
        "Required 8 weeks prior to QSA. Combined with overdue MS-1060 and MS-1070, this is a cascading failure risk.",
        styles=styles
    ))

    story.append(make_question(
        "4", "MS-S-160 \u2014 Modeling Data Submission (South) \u2014 Due Mar 3 (6 days, 0%)",
        "Given North's identical activity is already overdue, are we repeating the same delay? Is the same team responsible for both?",
        styles=styles
    ))

    story.append(make_question(
        "5", "MS-S-180 \u2014 Reactive Power Study (South) \u2014 Due Mar 17 (20 days, 0%)",
        "Has the SOW been issued? Same consultant as North? If consultant can't do both, we need a second resource.",
        styles=styles
    ))

    story.append(make_question(
        "6", "Permit-230 \u2014 Interconnect Agreement (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 13, 0%)",
        "Was scheduled to start Feb 17. What does Repsol's permit schedule say for IA execution? Is there a commercial disagreement?",
        "No IA = no backfeed, no injection.",
        styles=styles
    ))

    # ── CATEGORY B ──
    story.append(Paragraph(
        "CATEGORY B: PERMITS &amp; REGULATORY (NON-ERCOT)",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "7", "Permit-180 \u2014 NHPA/THC Cultural Resources (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 13, 0%)",
        "Scheduled start was Oct 31, 2025 (117 days ago). Has a Phase I cultural survey been done? THC contacted?",
        styles=styles
    ))

    story.append(make_question(
        "8", "Permit-210 \u2014 USACE CWA Section 404 Permit (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 13, 0%)",
        "Has wetland delineation been performed? Nationwide or Individual Permit? If Individual, timeline is 12\u201318 months.",
        styles=styles
    ))

    story.append(make_question(
        "9", "Permit-220 \u2014 TCEQ CWA Section 401 Certification (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 13, 0%)",
        "Has application been submitted? Interdependent with 404 process.",
        styles=styles
    ))

    story.append(make_question(
        "10", "Permit-130 \u2014 Floodplain Determination (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Jun 30, 0%)",
        "Scheduled start Feb 17 (8 days ago). Any FEMA-mapped floodplain encroachment?",
        styles=styles
    ))

    story.append(make_question(
        "11", "Permit-140 \u2014 Road Use Agreement (North) \u2014 <font color='#C0392B'>OVERDUE</font> (due Feb 23, 20%)",
        "Due 2 days ago, only 20%. What's the sticking point? When will it be executed?",
        styles=styles
    ))

    story.append(make_question(
        "12", "Permit-250 \u2014 Pipeline/Utility Crossing Agreements (North) \u2014 <font color='#E67E22'>BEHIND</font> (due Jun 15, 0%)",
        "How many crossings exist? Have pipeline operators been contacted?",
        styles=styles
    ))

    story.append(make_question(
        "13", "Permit-260 \u2014 Permanent Utility Relocation (North) \u2014 <font color='#E67E22'>BEHIND</font> (due May 29, 0%)",
        "Are there utilities needing relocation? Typical lead time is 6\u201312 months.",
        styles=styles
    ))

    story.append(make_question(
        "14", "Permit-130 (South) \u2014 SWPPP NOI #2 \u2014 <font color='#E67E22'>BEHIND</font> (due Feb 27, 0%)",
        "South mob is Mar 2. No SWPPP = no ground disturbance. Has NOI been filed with TCEQ?",
        styles=styles
    ))

    # ── CATEGORY C ──
    story.append(Paragraph(
        "CATEGORY C: ENGINEERING / IFC PACKAGES",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "15", "ENG-D-1031 \u2014 Civil 90% North Post ALTA \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 6, 0%)",
        "Started Dec 12 (75 days ago), 0% complete. Is the ALTA survey the blocker?",
        styles=styles
    ))

    story.append(make_question(
        "16", "ENG-1230 \u2014 Civil 90% North \u2014 Due Mar 6 (48%)",
        "At 48% with 9 days left \u2014 is this actual deliverable completion or elapsed time?",
        styles=styles
    ))

    story.append(make_question(
        "17", "ENG-1240 \u2014 PV Structural 90% North \u2014 Due Mar 6 (57%)",
        "What structural calcs/drawings are outstanding? Has tracker vendor provided loading requirements?",
        styles=styles
    ))

    story.append(make_question(
        "18", "ENG-1290 \u2014 PV Electrical 90% Owner Comments North \u2014 Due Mar 2 (10%)",
        "Only 10% with 5 days left. Is Repsol reviewing or is this on our side?",
        styles=styles
    ))

    story.append(make_question(
        "19", "ENG-1260 \u2014 PV SCADA 60% North \u2014 Due Feb 27 (68%)",
        "Due in 2 days at 68%. Will this be submitted on time?",
        styles=styles
    ))

    story.append(make_question(
        "20", "ENG-1420 \u2014 Substation Physical 90% Owner Comments North \u2014 Due Feb 27 (20%)",
        "Only 20% and due in 2 days. Are we waiting on Repsol?",
        styles=styles
    ))

    story.append(make_question(
        "21", "ENG-1390 \u2014 Substation P&amp;C 90% North \u2014 Due Mar 11 (65%)",
        "Is relay settings coordination complete? Protection scheme agreed with utility?",
        styles=styles
    ))

    story.append(make_question(
        "22", "ENG-1530 \u2014 OH Collection Line 90% Owner Comments South \u2014 <font color='#C0392B'>OVERDUE</font> (due Feb 18, 90%)",
        "What's the last 10%? Identify the blocking comment.",
        styles=styles
    ))

    story.append(make_question(
        "23", "ENG-1540 \u2014 OH Collection Line IFC South \u2014 <font color='#E67E22'>BEHIND</font> (due Mar 2, 0%)",
        "Blocked by ENG-1530? When will IFC transmit?",
        styles=styles
    ))

    story.append(make_question(
        "24\u201328", "South Engineering (ENG-D-1031, D-1032, 1230, 1240, 1250) \u2014 ALL at 0% with start dates 75\u2013188 days ago",
        "Are these data entry errors or has South engineering genuinely not started?",
        styles=styles
    ))

    story.append(make_question(
        "29", "ENG-1151 \u2014 Pile Plot Plan North \u2014 Starts Mar 20",
        "Will civil (0%) and structural (57%) inputs be ready? Who prepares PPPs?",
        styles=styles
    ))

    # ── CATEGORY D ──
    story.append(Paragraph(
        "CATEGORY D: PROCUREMENT / MATERIAL DELIVERY",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "30\u201335", "SIX NORTH ORDERS NOT YET PLACED (all scheduled to start Feb 18\u201323, all at 0%)",
        "Fiber Optic Cable, CAB/Messenger Cable, SCADA Panel, DC Collection Cable 50%, Substation Bus Material, Turning Pole \u2014 None of these orders have been placed. Provide a purchase order schedule for each.",
        styles=styles
    ))

    story.append(make_question(
        "36\u201339", "North Items in Progress: Racking (42%), Inverter Piles (64%), Production Piles (starts Mar 9), AC Cable (2%)",
        "What are the expected delivery dates for each? Is AC Cable at 2% a concern given construction timeline?",
        styles=styles
    ))

    story.append(make_question(
        "40", "SIX SOUTH T-LINE ORDERS \u2014 All scheduled Feb 18 start, all at 0%",
        "Have ANY been initiated? Provide status on each order.",
        styles=styles
    ))

    story.append(make_question(
        "41\u201344", "OWNER-FURNISHED ITEMS: MPT (83%), HV Breakers (18%), HV Switch (54%), MV Switch (54%)",
        "All need Repsol status updates. HV Breakers at only 18% is a critical concern \u2014 what is the delivery timeline?",
        styles=styles
    ))

    story.append(make_question(
        "45\u201348", "South Procurement: Production Piles 25% (31%), AC Cable (15%), DC Cable (4%), Racking (19%)",
        "All significantly behind expected progress. What are revised delivery commitments for each?",
        styles=styles
    ))

    # ── CATEGORY E ──
    story.append(Paragraph(
        "CATEGORY E: SUBCONTRACTOR MOBILIZATION &amp; FIELD",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "49", "South Contractor Mobilization \u2014 Mar 2 (5 days)",
        "NTP issued? Pre-mob requirements met? Site access confirmed?",
        styles=styles
    ))

    story.append(make_question(
        "50", "North Clearing &amp; Grubbing \u2014 7 days late, 0%",
        "Subcontractor engaged? What is the hold? Revised start date?",
        styles=styles
    ))

    story.append(make_question(
        "51\u201363", "Civil Execution Items",
        "Test pile removal, stump removal, fence removal, road upgrades, SWPPP install, phase gates \u2014 provide status and schedule for each of the 13 civil execution activities currently tracked.",
        styles=styles
    ))

    # ── CATEGORY F ──
    story.append(Paragraph(
        "CATEGORY F: OWNER / COMMERCIAL",
        styles['CategoryHeader']
    ))

    story.append(make_question(
        "64", "Acceleration Change Order",
        "Has Repsol responded? What is the dollar value? What is the fallback plan if not approved?",
        styles=styles
    ))

    story.append(make_question(
        "65", "Owner Permit Schedule",
        "Does Repsol's permit schedule align with the construction schedule? Identify any gaps.",
        styles=styles
    ))

    story.append(make_question(
        "66\u201367", "MPT and HV Breaker Delivery Confirmation",
        "Confirm delivery dates for both owner-furnished items. MPT at 83% \u2014 when does it ship? HV Breakers at 18% \u2014 is this on track?",
        styles=styles
    ))

    story.append(make_question(
        "68", "South QSA Deadline Apr 30",
        "Is there a realistic path to meet the Apr 30 QSA deadline given current progress on modeling data, FIS, and reactive power studies?",
        styles=styles
    ))


def add_list2(story, styles):
    """Add List 2: Production Analysis Follow-ups."""
    story.append(PageBreak())
    story.append(Paragraph(
        "LIST 2: PRODUCTION ANALYSIS FOLLOW-UPS (25 Questions)",
        styles['ListHeader']
    ))
    story.append(HRFlowable(width="100%", thickness=2, color=DARK_BLUE, spaceAfter=6))

    # A
    story.append(Paragraph(
        "A. CURRENT EARLY-PHASE PRODUCTION",
        styles['CategoryHeader']
    ))
    story.append(make_question("1", "Fence Removal Rate",
        "What is the current daily removal rate? How many linear feet remain?", styles=styles))
    story.append(make_question("2", "Clearing Readiness",
        "Is clearing equipment mobilized? Acreage remaining vs. planned?", styles=styles))
    story.append(make_question("3", "Test Pile Removal Plan",
        "How many test piles remain? Schedule for removal and area restoration?", styles=styles))

    # B
    story.append(Paragraph(
        "B. PRODUCTION RAMP PLANNING",
        styles['CategoryHeader']
    ))
    story.append(make_question("4", "Pile Driving Plan \u2014 North",
        "Planned start date, crew size, daily target, total pile count?", styles=styles))
    story.append(make_question("5", "Pile Driving Plan \u2014 South",
        "Planned start date, crew size, daily target, total pile count?", styles=styles))
    story.append(make_question("6", "Racking Installation Plan",
        "When does racking begin? What is the planned install rate (tables/day)?", styles=styles))
    story.append(make_question("7", "Module Installation Plan",
        "Target start date, crew size, daily MW target, total module count?", styles=styles))
    story.append(make_question("8", "Electrical Installation Plan",
        "DC stringing start, AC cable pull schedule, inverter commissioning plan?", styles=styles))

    # C
    story.append(Paragraph(
        "C. EQUIPMENT &amp; RESOURCES",
        styles['CategoryHeader']
    ))
    story.append(make_question("9", "Equipment on Site \u2014 North",
        "Current equipment inventory. What is on site vs. what is needed for next 30 days?", styles=styles))
    story.append(make_question("10", "South Mobilization Equipment",
        "Equipment list for South mob. Delivery dates confirmed?", styles=styles))
    story.append(make_question("11", "Crane Planning",
        "Crane requirements for substation, T-line, and tracker installation. Availability confirmed?", styles=styles))
    story.append(make_question("12", "PD-10 Rig Availability",
        "How many pile driving rigs are planned? Lead time for additional rigs if needed?", styles=styles))

    # D
    story.append(Paragraph(
        "D. WEATHER &amp; GROUND CONDITIONS",
        styles['CategoryHeader']
    ))
    story.append(make_question("13", "Weather Impact",
        "Lost weather days in last 30 days? Forecast for next 2 weeks? Contingency plan?", styles=styles))
    story.append(make_question("14", "Geotech Summary",
        "Key geotech findings for both North and South. Any surprises affecting pile design or civil work?", styles=styles))
    story.append(make_question("15", "Flooding / Drainage Risk",
        "Any standing water issues? Drainage plan adequate for current site conditions?", styles=styles))

    # E
    story.append(Paragraph(
        "E. QUALITY / REWORK",
        styles['CategoryHeader']
    ))
    story.append(make_question("16", "Survey Control",
        "Is survey control established for both phases? Any monument issues?", styles=styles))
    story.append(make_question("17", "Test Pile Results",
        "Summary of test pile program results. Any design changes required?", styles=styles))
    story.append(make_question("18", "QC Staffing",
        "Current QC headcount vs. plan. Adequate for upcoming ramp?", styles=styles))
    story.append(make_question("19", "Module Receiving Inspection",
        "Inspection plan for module deliveries. Storage and handling procedures confirmed?", styles=styles))
    story.append(make_question("20", "Racking QC",
        "Torque verification plan, tracker alignment tolerances, inspection frequency?", styles=styles))

    # F
    story.append(Paragraph(
        "F. SAFETY &amp; LABOR",
        styles['CategoryHeader']
    ))
    story.append(make_question("21", "South Safety Readiness",
        "Site-specific safety plan approved? Emergency action plan in place?", styles=styles))
    story.append(make_question("22", "Labor Availability",
        "Current headcount vs. plan for both phases. Hiring pipeline status?", styles=styles))
    story.append(make_question("23", "Safety Manpower Ratio",
        "Current safety-to-craft ratio. Will it hold as headcount ramps?", styles=styles))
    story.append(make_question("24", "Subcontractor Prequalification",
        "All subs prequalified? Any outstanding safety or insurance issues?", styles=styles))
    story.append(make_question("25", "Training",
        "Orientation completion rate? Specialized training (electrical, crane signals) scheduled?", styles=styles))


def add_urgent_summary(story, styles):
    """Add the Urgent Summary section with visual emphasis."""
    story.append(PageBreak())

    # Red banner header
    story.append(Paragraph(
        "\u26A0  URGENT SUMMARY \u2014 NEED ANSWERS BY END OF WEEK",
        styles['UrgentSectionHeader']
    ))
    story.append(HRFlowable(width="100%", thickness=2.5, color=ACCENT_RED, spaceAfter=10))

    # ── RESOLVE IMMEDIATELY ──
    resolve_data = [
        [Paragraph("<b>RESOLVE IMMEDIATELY (Overdue)</b>",
                    ParagraphStyle('rh', fontSize=10, textColor=WHITE, fontName='Helvetica-Bold'))],
    ]
    resolve_header = Table(resolve_data, colWidths=[6.5 * inch])
    resolve_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), ACCENT_RED),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(resolve_header)

    resolve_items = [
        ["MS-1060", "North Modeling Data to ERCOT", "7 days overdue"],
        ["MS-1070", "North FIS", "2 days overdue"],
        ["ENG-1530", "South OH Collection Line comments", "7 days overdue"],
        ["Permit-140", "Road Use Agreement", "2 days overdue"],
    ]
    for code, desc, status in resolve_items:
        row_data = [[
            Paragraph(f"<b>{code}</b>", ParagraphStyle('rc', fontSize=9, textColor=ACCENT_RED, fontName='Helvetica-Bold')),
            Paragraph(desc, ParagraphStyle('rd', fontSize=9, textColor=DARK_GRAY)),
            Paragraph(f"<i>{status}</i>", ParagraphStyle('rs', fontSize=8, textColor=ACCENT_RED, fontName='Helvetica-Oblique')),
        ]]
        row_table = Table(row_data, colWidths=[1.2 * inch, 3.8 * inch, 1.5 * inch])
        row_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_RED),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(row_table)

    story.append(Spacer(1, 14))

    # ── CONFIRM BY FRIDAY ──
    confirm_data = [
        [Paragraph("<b>CONFIRM BY FRIDAY</b>",
                    ParagraphStyle('ch', fontSize=10, textColor=WHITE, fontName='Helvetica-Bold'))],
    ]
    confirm_header = Table(confirm_data, colWidths=[6.5 * inch])
    confirm_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), ACCENT_ORANGE),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(confirm_header)

    confirm_items = [
        ["MS-1080", "Reactive Power Study", "Due Feb 28"],
        ["ENG-1260", "SCADA 60%", "Due Feb 27"],
        ["ENG-1420", "Substation owner comments", "Due Feb 27"],
        ["Accel. CO", "Acceleration Change Order response", "Pending Repsol"],
    ]
    for code, desc, status in confirm_items:
        row_data = [[
            Paragraph(f"<b>{code}</b>", ParagraphStyle('cc', fontSize=9, textColor=ACCENT_ORANGE, fontName='Helvetica-Bold')),
            Paragraph(desc, ParagraphStyle('cd', fontSize=9, textColor=DARK_GRAY)),
            Paragraph(f"<i>{status}</i>", ParagraphStyle('cs', fontSize=8, textColor=ACCENT_ORANGE, fontName='Helvetica-Oblique')),
        ]]
        row_table = Table(row_data, colWidths=[1.2 * inch, 3.8 * inch, 1.5 * inch])
        row_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_ORANGE),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(row_table)

    story.append(Spacer(1, 14))

    # ── SOUTH MOB GO/NO-GO ──
    mob_data = [
        [Paragraph("<b>SOUTH MOB GO/NO-GO (Mar 2)</b>",
                    ParagraphStyle('mh', fontSize=10, textColor=WHITE, fontName='Helvetica-Bold'))],
    ]
    mob_header = Table(mob_data, colWidths=[6.5 * inch])
    mob_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), DARK_BLUE),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(mob_header)

    mob_items = [
        "SWPPP NOI filed?",
        "IFC transmitted?",
        "Contractor confirmed?",
        "Laydown ready?",
    ]
    for item in mob_items:
        row_data = [[
            Paragraph("\u25A0", ParagraphStyle('mb', fontSize=10, textColor=DARK_BLUE)),
            Paragraph(item, ParagraphStyle('mi', fontSize=9, textColor=DARK_GRAY)),
            Paragraph("\u2610", ParagraphStyle('mc', fontSize=14, textColor=MEDIUM_GRAY)),
        ]]
        row_table = Table(row_data, colWidths=[0.3 * inch, 5.0 * inch, 1.2 * inch])
        row_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_BLUE),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(row_table)

    story.append(Spacer(1, 14))

    # ── ESCALATE TO REPSOL ──
    esc_data = [
        [Paragraph("<b>ESCALATE TO REPSOL</b>",
                    ParagraphStyle('eh', fontSize=10, textColor=WHITE, fontName='Helvetica-Bold'))],
    ]
    esc_header = Table(esc_data, colWidths=[6.5 * inch])
    esc_header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor("#8E44AD")),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(esc_header)

    esc_items = [
        ["Permit Schedule", "Alignment with construction schedule"],
        ["Accel. CO", "Acceleration CO deadline"],
        ["MPT", "Delivery status (83%)"],
        ["HV Breakers", "Delivery status (18% \u2014 critical concern)"],
    ]
    for item, desc in esc_items:
        row_data = [[
            Paragraph(f"<b>{item}</b>", ParagraphStyle('ec', fontSize=9, textColor=HexColor("#8E44AD"), fontName='Helvetica-Bold')),
            Paragraph(desc, ParagraphStyle('ed', fontSize=9, textColor=DARK_GRAY)),
        ]]
        row_table = Table(row_data, colWidths=[1.5 * inch, 5.0 * inch])
        row_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), HexColor("#F5EEF8")),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LINEBELOW', (0, 0), (-1, -1), 0.3, BORDER_GRAY),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(row_table)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    doc = SimpleDocTemplate(
        OUTPUT_FILE,
        pagesize=letter,
        topMargin=0.85 * inch,
        bottomMargin=0.8 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        title="Pecan Prairie Solar — DSC Operations Lead Question Bank",
        author="DSC Operations",
        subject="APM Questions — February 25, 2026",
    )

    styles = build_styles()
    story = []

    # Cover page
    add_cover_page(story, styles)

    # Table of Contents
    add_toc_page(story, styles)

    # List 1: Critical Constraint Questions
    add_list1(story, styles)

    # List 2: Production Follow-ups
    add_list2(story, styles)

    # Urgent Summary
    add_urgent_summary(story, styles)

    # Build with page numbers
    doc.build(story, onFirstPage=first_page_header_footer, onLaterPages=header_footer)
    print(f"PDF generated: {OUTPUT_FILE}")
    print(f"File size: {os.path.getsize(OUTPUT_FILE):,} bytes")


if __name__ == "__main__":
    main()
