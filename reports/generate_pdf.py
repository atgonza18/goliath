#!/usr/bin/env python3
"""
GOLIATH Portfolio Schedule Health Report — PDF Generator
Generates a professional PDF report from portfolio schedule data using ReportLab.
"""

import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# ---------------------------------------------------------------------------
# Brand colours
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#1B365D")
DARK_NAVY = colors.HexColor("#0F1F3D")
RED = colors.HexColor("#CC0000")
AMBER = colors.HexColor("#CC8800")
GREEN = colors.HexColor("#228B22")
LIGHT_GREY = colors.HexColor("#F5F5F5")
MID_GREY = colors.HexColor("#E0E0E0")
WHITE = colors.white
BLACK = colors.black
SOFT_RED_BG = colors.HexColor("#FFF0F0")
SOFT_AMBER_BG = colors.HexColor("#FFF8E8")
SOFT_GREEN_BG = colors.HexColor("#F0FFF0")
TABLE_HEADER_BG = colors.HexColor("#1B365D")
TABLE_ALT_ROW = colors.HexColor("#F2F6FA")

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "2026-02-26-portfolio-schedule-report.pdf")

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
_styles = getSampleStyleSheet()


def _style(name, **kw):
    """Create a ParagraphStyle, falling back to Normal as parent."""
    parent = _styles["Normal"]
    return ParagraphStyle(name, parent=parent, **kw)


STYLE_TITLE = _style("Title2", fontName="Helvetica-Bold", fontSize=22,
                      textColor=WHITE, leading=28, alignment=TA_LEFT)
STYLE_SUBTITLE = _style("Subtitle2", fontName="Helvetica", fontSize=11,
                         textColor=colors.HexColor("#B0C4DE"), leading=14,
                         alignment=TA_LEFT)
STYLE_H1 = _style("H1x", fontName="Helvetica-Bold", fontSize=16,
                   textColor=NAVY, leading=22, spaceBefore=18, spaceAfter=6)
STYLE_H2 = _style("H2x", fontName="Helvetica-Bold", fontSize=13,
                   textColor=NAVY, leading=18, spaceBefore=14, spaceAfter=4)
STYLE_H3 = _style("H3x", fontName="Helvetica-Bold", fontSize=11,
                   textColor=NAVY, leading=15, spaceBefore=10, spaceAfter=3)
STYLE_BODY = _style("Bodyx", fontName="Helvetica", fontSize=9.5,
                     textColor=BLACK, leading=13, spaceAfter=3)
STYLE_BODY_BOLD = _style("BodyBold", fontName="Helvetica-Bold", fontSize=9.5,
                          textColor=BLACK, leading=13, spaceAfter=3)
STYLE_BULLET = _style("Bullet2", fontName="Helvetica", fontSize=9.5,
                       textColor=BLACK, leading=13, leftIndent=18,
                       bulletIndent=6, spaceAfter=2, bulletFontSize=9)
STYLE_SMALL = _style("Smallx", fontName="Helvetica", fontSize=8,
                      textColor=colors.HexColor("#666666"), leading=10)
STYLE_TABLE_HEADER = _style("TH", fontName="Helvetica-Bold", fontSize=8.5,
                             textColor=WHITE, leading=11, alignment=TA_LEFT)
STYLE_TABLE_CELL = _style("TC", fontName="Helvetica", fontSize=8.5,
                           textColor=BLACK, leading=11, alignment=TA_LEFT)
STYLE_TABLE_CELL_BOLD = _style("TCBold", fontName="Helvetica-Bold",
                                fontSize=8.5, textColor=BLACK, leading=11,
                                alignment=TA_LEFT)
STYLE_RISK_CRITICAL = _style("RCrit", fontName="Helvetica-Bold", fontSize=9,
                              textColor=RED, leading=12)
STYLE_RISK_MODERATE = _style("RMod", fontName="Helvetica-Bold", fontSize=9,
                              textColor=AMBER, leading=12)
STYLE_RISK_LOW = _style("RLow", fontName="Helvetica-Bold", fontSize=9,
                          textColor=GREEN, leading=12)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def risk_color(status_text: str) -> colors.HexColor:
    s = status_text.upper()
    if "CRITICAL" in s or "EXTREME" in s or "HIGH" in s:
        return RED
    if "MODERATE" in s or "WATCH" in s or "BEHIND" in s or "DELAYED" in s:
        return AMBER
    if "ON TRACK" in s or "LOW" in s or "COMPLETE" in s:
        return GREEN
    return BLACK


def risk_bg(status_text: str) -> colors.HexColor:
    s = status_text.upper()
    if "CRITICAL" in s or "EXTREME" in s or "HIGH" in s:
        return SOFT_RED_BG
    if "MODERATE" in s or "WATCH" in s or "BEHIND" in s or "DELAYED" in s:
        return SOFT_AMBER_BG
    if "ON TRACK" in s or "LOW" in s or "COMPLETE" in s:
        return SOFT_GREEN_BG
    return WHITE


def colored_status(text: str) -> str:
    c = risk_color(text)
    hex_c = c.hexval() if hasattr(c, "hexval") else str(c)
    return f'<font color="{hex_c}"><b>{text}</b></font>'


def _p(text, style=STYLE_TABLE_CELL):
    return Paragraph(str(text), style)


def _ph(text):
    return Paragraph(str(text), STYLE_TABLE_HEADER)


def _pc(text):
    """Cell with colour-coded status."""
    c = risk_color(str(text))
    hex_c = c.hexval() if hasattr(c, "hexval") else str(c)
    st = ParagraphStyle("dyn", parent=STYLE_TABLE_CELL,
                         fontName="Helvetica-Bold", textColor=c)
    return Paragraph(str(text), st)


def build_table(headers, rows, col_widths=None):
    """Build a professional-looking table with alternating row shading."""
    data = [[_ph(h) for h in headers]]
    for row in rows:
        data.append([_p(c) if i < len(row) - 1 else _pc(row[-1])
                      for i, c in enumerate(row)])

    available_width = 7.0 * inch
    if col_widths is None:
        n = len(headers)
        col_widths = [available_width / n] * n

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    # Alternating row shading
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    return t


def build_simple_table(headers, rows, col_widths=None):
    """Table where last column is NOT auto-colour-coded."""
    data = [[_ph(h) for h in headers]]
    for row in rows:
        data.append([_p(c) for c in row])

    available_width = 7.0 * inch
    if col_widths is None:
        n = len(headers)
        col_widths = [available_width / n] * n

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    return t


def section_divider():
    return HRFlowable(width="100%", thickness=1.5, color=NAVY,
                      spaceBefore=12, spaceAfter=12)


def thin_divider():
    return HRFlowable(width="100%", thickness=0.5, color=MID_GREY,
                      spaceBefore=6, spaceAfter=6)


def risk_badge(text):
    """Return a Paragraph showing a colour-coded risk badge."""
    c = risk_color(text)
    hex_c = c.hexval() if hasattr(c, "hexval") else str(c)
    st = ParagraphStyle("badge", parent=STYLE_BODY, fontName="Helvetica-Bold",
                         fontSize=10, textColor=c, leading=14,
                         spaceBefore=4, spaceAfter=8)
    return Paragraph(f"Risk Assessment: {text}", st)

# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

def _header_footer(canvas, doc):
    """Draw header bar and footer on every page."""
    canvas.saveState()
    w, h = letter

    # Header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 50, w, 50, stroke=0, fill=1)
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(0.75 * inch, h - 33, "GOLIATH Portfolio Schedule Health Report")
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#B0C4DE"))
    canvas.drawRightString(w - 0.75 * inch, h - 33,
                           "February 26, 2026  |  Data Dates: Feb 18-23, 2026")

    # Footer
    canvas.setStrokeColor(MID_GREY)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.55 * inch, w - 0.75 * inch, 0.55 * inch)
    canvas.setFillColor(colors.HexColor("#999999"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(0.75 * inch, 0.38 * inch,
                      "Generated by GOLIATH \u2014 Autonomous Construction Portfolio Management System")
    canvas.drawRightString(w - 0.75 * inch, 0.38 * inch,
                           f"Page {doc.page}")
    canvas.restoreState()


def _first_page(canvas, doc):
    """Cover-page style header (taller) + footer."""
    canvas.saveState()
    w, h = letter

    # Tall header block
    header_h = 110
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - header_h, w, header_h, stroke=0, fill=1)

    # Accent line
    canvas.setFillColor(colors.HexColor("#3A7BD5"))
    canvas.rect(0, h - header_h, w, 3, stroke=0, fill=1)

    # Title text
    canvas.setFillColor(WHITE)
    canvas.setFont("Helvetica-Bold", 26)
    canvas.drawString(0.75 * inch, h - 50, "GOLIATH")
    canvas.setFont("Helvetica", 16)
    canvas.drawString(0.75 * inch, h - 72,
                      "Portfolio Schedule Health Report")
    canvas.setFont("Helvetica", 11)
    canvas.setFillColor(colors.HexColor("#B0C4DE"))
    canvas.drawString(0.75 * inch, h - 92,
                      "February 26, 2026  |  Data Dates: Feb 18-23, 2026")

    # Footer (same as other pages)
    canvas.setStrokeColor(MID_GREY)
    canvas.setLineWidth(0.5)
    canvas.line(0.75 * inch, 0.55 * inch, w - 0.75 * inch, 0.55 * inch)
    canvas.setFillColor(colors.HexColor("#999999"))
    canvas.setFont("Helvetica", 7)
    canvas.drawString(0.75 * inch, 0.38 * inch,
                      "Generated by GOLIATH \u2014 Autonomous Construction Portfolio Management System")
    canvas.drawRightString(w - 0.75 * inch, 0.38 * inch,
                           f"Page {doc.page}")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Content builders
# ---------------------------------------------------------------------------

def build_executive_summary():
    """Return a list of flowables for the executive summary."""
    elements = []
    elements.append(Paragraph("EXECUTIVE SUMMARY", STYLE_H1))
    elements.append(thin_divider())

    elements.append(Paragraph(
        "<b>Projects with Schedule Data: 4 of 12</b>", STYLE_BODY_BOLD))
    elements.append(Spacer(1, 4))

    bullets = [
        (f"<b>Blackford Solar</b> (211 MW DC) \u2014 White Construction \u2014 "
         f"{colored_status('CRITICAL')}: -53 days overall float"),
        (f"<b>Duff Solar</b> (138 MW DC) \u2014 White Construction \u2014 "
         f"{colored_status('CRITICAL')}: -158 days on circuit completions"),
        (f"<b>Pecan Prairie North</b> (407 MW DC) \u2014 Wanzek \u2014 "
         f"{colored_status('On Track')} (early stage)"),
        (f"<b>Pecan Prairie South</b> (188 MW DC) \u2014 Wanzek \u2014 "
         f"{colored_status('On Track')} (early stage)"),
    ]
    for b in bullets:
        elements.append(Paragraph(b, STYLE_BULLET, bulletText="\u2022"))

    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        "<b>Projects with NO data uploaded:</b> Union Ridge, Salt Branch, "
        "Delta Bobcat, Tehuacana, Three Rivers, Mayes, Graceland, Duffy BESS",
        STYLE_BODY))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        '<font color="#CC8800"><b>Data Anomaly:</b></font> Scioto Ridge folder '
        'contains duplicate Blackford files \u2014 needs correction.',
        STYLE_BODY))
    elements.append(Spacer(1, 6))
    return elements


def build_blackford():
    """Section 1: Blackford Solar."""
    elements = []
    elements.append(section_divider())
    elements.append(Paragraph("1. BLACKFORD SOLAR \u2014 211.21 MW DC (38 Blocks)", STYLE_H1))

    info_lines = [
        "<b>Contractor:</b> White Construction (a MasTec company)",
        "<b>Data Date:</b> February 23, 2026",
        "<b>Project Duration:</b> April 18, 2024 \u2192 September 18, 2026 (627 calendar days)",
        f"<b>Overall Float:</b> {colored_status('-53 days  CRITICAL')}",
    ]
    for line in info_lines:
        elements.append(Paragraph(line, STYLE_BODY))
    elements.append(Spacer(1, 8))

    # Key Milestones
    elements.append(Paragraph("Key Milestones", STYLE_H2))
    headers = ["Milestone", "Contract Date", "Current Forecast", "Float", "Status"]
    widths = [1.7*inch, 1.15*inch, 1.25*inch, 0.7*inch, 1.0*inch]
    # Ensure total ~7 inch
    # 1.7 + 1.15 + 1.25 + 0.7 + 1.0 = 5.8  -> stretch a bit
    widths = [1.85*inch, 1.2*inch, 1.3*inch, 0.75*inch, 0.9*inch]
    rows = [
        ["Backfeed / Interconnect", "Jan 19, 2026", "Jun 1, 2026", "-94", "CRITICAL"],
        ["HV Works Completion", "Jan 19, 2026", "Jun 1, 2026", "-94", "CRITICAL"],
        ["1st Circuit MC", "Nov 20, 2025", "Mar 25, 2026", "-79", "CRITICAL"],
        ["2nd Circuit MC", "Dec 18, 2025", "Apr 13, 2026", "-17", "Behind"],
        ["3rd Circuit MC", "Jan 20, 2026", "Apr 27, 2026", "-25", "Behind"],
        ["4th Circuit MC", "Feb 10, 2026", "May 7, 2026", "-31", "Behind"],
        ["5th Circuit MC", "Mar 5, 2026", "May 21, 2026", "-38.5", "Behind"],
        ["6th Circuit MC", "Mar 26, 2026", "Jun 2, 2026", "-44", "Behind"],
        ["7th Circuit MC", "Apr 16, 2026", "Jun 10, 2026", "-48", "Behind"],
        ["8th Circuit MC", "Apr 29, 2026", "Jun 18, 2026", "-52", "Behind"],
        ["Project MC", "\u2014", "Jun 19, 2026", "-20", "Behind"],
        ["COD", "Jun 5, 2026", "Aug 6, 2026", "-53", "CRITICAL"],
        ["Substantial Completion", "Jun 5, 2026", "Aug 6, 2026", "-53", "CRITICAL"],
        ["Final Completion", "Jul 6, 2026", "Sep 18, 2026", "-53", "CRITICAL"],
    ]
    elements.append(build_table(headers, rows, widths))
    elements.append(Spacer(1, 10))

    # Phase Status
    elements.append(Paragraph("Phase Status", STYLE_H2))
    ph_headers = ["Phase", "Status", "Start", "Finish"]
    ph_widths = [2.0*inch, 1.3*inch, 1.5*inch, 1.5*inch]
    ph_rows = [
        ["Engineering/Design", "COMPLETE", "May 4, 2024", "Aug 30, 2025"],
        ["Permits", "COMPLETE", "Mar 12, 2025", "Aug 29, 2025"],
        ["Procurement", "In Progress", "Sep 13, 2024", "Mar 23, 2026"],
        ["Construction", "Active", "Aug 4, 2025", "Sep 18, 2026"],
        ["Testing & Commissioning", "Future", "Mar 6, 2026", "Aug 6, 2026"],
    ]
    elements.append(build_table(ph_headers, ph_rows, ph_widths))
    elements.append(Spacer(1, 10))

    # Active Construction
    elements.append(Paragraph("Active Construction Activities", STYLE_H2))
    elements.append(Paragraph("<b>In Progress:</b>", STYLE_BODY))
    constr = [
        "Fencing: 51 days remaining (finish May 5, 2026) \u2014 Float: -3",
        "Civil (Site Cut/Fill): 14 days remaining (finish Mar 13, 2026) \u2014 Float: -73.5",
        "SWPPP Install: 60 days remaining (finish May 18, 2026) \u2014 Float: -143",
        "Pile Foundations: 21 days remaining (finish Mar 31, 2026) \u2014 Float: -9.5",
        "Array Construction: 67 days remaining (finish May 28, 2026) \u2014 Float: -38.5",
        "DC Cable Terminations: 54 days remaining \u2014 Float: -24",
        "MVAC Cable Terminations: 54 days remaining \u2014 Float: -30",
    ]
    for c in constr:
        elements.append(Paragraph(c, STYLE_BULLET, bulletText="\u2022"))
    elements.append(Spacer(1, 8))

    # Substation Status
    elements.append(Paragraph("Substation Status", STYLE_H3))
    sub = [
        "Equipment Install/Bus Work: 58 days remaining \u2014 Float: -88",
        "Testing & Commissioning: May 1 - Jun 1, 2026 \u2014 Float: -94",
        "Energization: Jun 1, 2026 \u2014 Float: -94",
    ]
    for s in sub:
        elements.append(Paragraph(s, STYLE_BULLET, bulletText="\u2022"))
    elements.append(Spacer(1, 8))

    # Procurement Outstanding
    elements.append(Paragraph("Procurement Outstanding", STYLE_H3))
    proc = [
        "Substation Steel Ship Time: 9 days \u2014 Float: -89",
        "Insulators/Station Posts: 20 days \u2014 Float: -77",
        "HV Circuit Breakers: 3 days \u2014 Float: -45",
        "MPT (By Owner): 9 days \u2014 Float: -65",
    ]
    for p in proc:
        elements.append(Paragraph(p, STYLE_BULLET, bulletText="\u2022"))
    elements.append(Spacer(1, 6))

    elements.append(risk_badge(
        "HIGH RISK \u2014 53+ days behind on critical path. "
        "Every MC date past contract. LD exposure significant."))
    return elements


def build_duff():
    """Section 2: Duff Solar."""
    elements = []
    elements.append(section_divider())
    elements.append(Paragraph("2. DUFF SOLAR \u2014 138 MW DC + Substation", STYLE_H1))

    info = [
        "<b>Contractor:</b> White Construction (a MasTec company)",
        "<b>Data Date:</b> February 23, 2026",
        "<b>Remaining Duration:</b> 125 work days",
        f"<b>Overall Float:</b> 0 (Final) but {colored_status('-158 on circuit completions  CRITICAL')}",
    ]
    for line in info:
        elements.append(Paragraph(line, STYLE_BODY))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Key Milestones", STYLE_H2))
    headers = ["Milestone", "Contract/LD Date", "Current Forecast", "Float", "Status"]
    widths = [1.6*inch, 1.2*inch, 1.3*inch, 0.7*inch, 1.0*inch]
    rows = [
        ["HV Completion", "Oct 30, 2025", "Jan 8, 2026", "\u2014", "COMPLETE"],
        ["1st Circuit MC", "Oct 23, 2025", "Apr 14, 2026", "-117", "CRITICAL"],
        ["2nd Circuit MC", "\u2014", "May 27, 2026", "-147", "CRITICAL"],
        ["3rd Circuit MC", "Nov 5, 2025", "Jun 24, 2026", "-158", "CRITICAL"],
        ["1st Circuit Energization", "\u2014", "May 29, 2026", "-116", "CRITICAL"],
        ["Substantial Completion", "Feb 6, 2026", "Jul 21, 2026", "-115", "CRITICAL"],
        ["Final Completion", "\u2014", "Aug 18, 2026", "0", "On Track"],
    ]
    elements.append(build_table(headers, rows, widths))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph("Active Construction (Worst Float Items)", STYLE_H2))
    act_headers = ["Activity", "Remaining Days", "Float"]
    act_widths = [3.0*inch, 1.5*inch, 1.5*inch]
    act_rows = [
        ["Place PV Modules", "78 days", "-158"],
        ["Messenger Wire Install", "74 days", "-158"],
        ["DC Rollout", "69 days", "-158"],
        ["Racking Construction", "81 days", "-152"],
        ["Pile Install (various)", "28 days", "-100 to -124"],
    ]
    elements.append(build_simple_table(act_headers, act_rows, act_widths))
    elements.append(Spacer(1, 6))

    elements.append(risk_badge(
        "EXTREME RISK \u2014 Worst in portfolio. -158 days on circuit "
        "completions. LD exposure severe."))
    return elements


def build_pecan_north():
    """Section 3: Pecan Prairie North."""
    elements = []
    elements.append(section_divider())
    elements.append(Paragraph("3. PECAN PRAIRIE NORTH \u2014 407 MW DC", STYLE_H1))

    info = [
        "<b>Contractor:</b> Wanzek (a MasTec company)",
        "<b>Data Date:</b> February 18, 2026",
        "<b>Duration:</b> 407 remaining days",
    ]
    for line in info:
        elements.append(Paragraph(line, STYLE_BODY))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Key Milestones", STYLE_H2))
    headers = ["Milestone", "Contract Date", "Current Forecast", "Status"]
    widths = [2.0*inch, 1.4*inch, 1.4*inch, 1.2*inch]
    rows = [
        ["Mobilization", "\u2014", "Sep 29, 2025", "COMPLETE"],
        ["MC 250 MW AC", "Dec 31, 2026", "Mar 5, 2027", "Watch"],
        ["Full MC", "Feb 19, 2027", "May 13, 2027", "Watch"],
        ["Substantial Completion", "Apr 30, 2027", "Jul 23, 2027", "Watch"],
        ["COD", "Jun 17, 2027", "Sep 9, 2027", "Watch"],
    ]
    elements.append(build_table(headers, rows, widths))
    elements.append(Spacer(1, 6))

    elements.append(risk_badge(
        "MODERATE RISK \u2014 ~84 days projected slippage on key milestones."))
    return elements


def build_pecan_south():
    """Section 4: Pecan Prairie South."""
    elements = []
    elements.append(section_divider())
    elements.append(Paragraph("4. PECAN PRAIRIE SOUTH \u2014 188 MW DC", STYLE_H1))

    info = [
        "<b>Contractor:</b> Wanzek (a MasTec company)",
        "<b>Data Date:</b> February 18, 2026",
        "<b>Duration:</b> 348 remaining days",
    ]
    for line in info:
        elements.append(Paragraph(line, STYLE_BODY))
    elements.append(Spacer(1, 8))

    elements.append(Paragraph("Key Milestones", STYLE_H2))
    headers = ["Milestone", "Contract Date", "Current Forecast", "Status"]
    widths = [2.0*inch, 1.4*inch, 1.4*inch, 1.2*inch]
    rows = [
        ["Mobilization", "Sep 29, 2025", "Mar 2, 2026", "Delayed"],
        ["T-Line Energization", "Nov 17, 2026", "Jan 7, 2027", "Watch"],
        ["MC", "Mar 12, 2027", "Mar 22, 2027", "On Track"],
        ["SC", "Apr 30, 2027", "May 10, 2027", "On Track"],
        ["Final Completion", "May 28, 2027", "Jun 30, 2027", "On Track"],
    ]
    elements.append(build_table(headers, rows, widths))
    elements.append(Spacer(1, 6))

    elements.append(risk_badge(
        "LOW-MODERATE RISK \u2014 Early stage, manageable slippage."))
    return elements


def build_portfolio_summary():
    """Portfolio summary table and key actions."""
    elements = []
    elements.append(section_divider())
    elements.append(Paragraph("PORTFOLIO SUMMARY", STYLE_H1))
    elements.append(thin_divider())

    headers = ["Project", "MW DC", "Contractor", "Float", "COD/SC Target", "Risk"]
    widths = [1.3*inch, 0.6*inch, 1.1*inch, 0.7*inch, 1.15*inch, 1.15*inch]
    rows = [
        ["Blackford", "211", "White/MasTec", "-53", "Aug 6, 2026", "HIGH"],
        ["Duff", "138", "White/MasTec", "-158", "Jul 21, 2026", "EXTREME"],
        ["Pecan North", "407", "Wanzek", "~-84", "Sep 9, 2027", "MODERATE"],
        ["Pecan South", "188", "Wanzek", "~-10", "May 10, 2027", "LOW-MOD"],
        ["8 projects", "\u2014", "\u2014", "\u2014", "\u2014", "NO DATA"],
    ]
    elements.append(build_table(headers, rows, widths))
    elements.append(Spacer(1, 14))

    elements.append(Paragraph("KEY ACTIONS", STYLE_H2))
    elements.append(Spacer(1, 4))

    actions = [
        ("<b>1. Duff Solar</b> \u2014 Immediate schedule recovery plan needed. "
         "<font color=\"#CC0000\">-158 days</font> on circuit completions."),
        ("<b>2. Blackford Solar</b> \u2014 Schedule recovery discussion needed. "
         "<font color=\"#CC0000\">-94</font> on backfeed."),
        ("<b>3. Pecan Prairie</b> \u2014 Monitor as construction ramps up. "
         "<font color=\"#CC8800\">~84 days</font> projected slip."),
        "<b>4. Data Collection</b> \u2014 8 projects have ZERO schedule data.",
        "<b>5. Scioto Ridge</b> \u2014 Incorrect files. Need correct schedule.",
        "<b>6. Constraints & POD</b> \u2014 All folders empty. Need data feeds.",
    ]
    for a in actions:
        elements.append(Paragraph(a, STYLE_BULLET, bulletText="\u2022"))

    elements.append(Spacer(1, 20))
    return elements


# ---------------------------------------------------------------------------
# Main PDF builder
# ---------------------------------------------------------------------------

def generate_report():
    """Generate the full PDF report."""

    w, h = letter

    # Frame for first page — offset down to account for taller header
    first_frame = Frame(
        0.75 * inch, 0.75 * inch,
        w - 1.5 * inch, h - 1.85 * inch,
        id="first",
    )
    # Frame for subsequent pages — standard header
    later_frame = Frame(
        0.75 * inch, 0.75 * inch,
        w - 1.5 * inch, h - 1.5 * inch,
        id="later",
    )

    first_page_tmpl = PageTemplate(id="First", frames=[first_frame],
                                    onPage=_first_page)
    later_page_tmpl = PageTemplate(id="Later", frames=[later_frame],
                                    onPage=_header_footer)

    doc = BaseDocTemplate(
        OUTPUT_PATH,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=1.5 * inch,
        bottomMargin=0.75 * inch,
        title="GOLIATH Portfolio Schedule Health Report",
        author="GOLIATH System",
    )
    doc.addPageTemplates([first_page_tmpl, later_page_tmpl])

    elements = []

    # After the first page content, switch template
    elements += build_executive_summary()
    elements.append(NextPageTemplate("Later"))

    elements += build_blackford()
    elements += build_duff()
    elements += build_pecan_north()
    elements += build_pecan_south()
    elements += build_portfolio_summary()

    doc.build(elements)
    print(f"PDF report generated: {OUTPUT_PATH}")
    print(f"File size: {os.path.getsize(OUTPUT_PATH):,} bytes")


if __name__ == "__main__":
    generate_report()
