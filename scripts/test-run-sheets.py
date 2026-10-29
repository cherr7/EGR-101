#!/usr/bin/env python3
"""Printable observer sheets: Test 4 (ease of use) for each Test 1 run, and the Test 5 reset log.

Reads output/test-labels/test-labels.csv and output/new-samples (reference labels for Test 5).
Usage: .venv/bin/python scripts/test-run-sheets.py [RUNS, default 6: five team runs + the UL site-visit run]
Writes output/test-sheets/run-sheets.pdf (US Letter, landscape).
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

ROOT = Path(__file__).resolve().parents[1]
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
OUT = ROOT / "output" / "test-sheets"
labels = list(csv.DictReader(open(ROOT / "output" / "test-labels" / "test-labels.csv")))
styles = getSampleStyleSheet()
small = styles["BodyText"].clone("small", fontSize=8.5, leading=10.5)
GRID = TableStyle([("GRID", (0, 0), (-1, -1), .5, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                   ("FONTSIZE", (0, 0), (-1, -1), 9), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                   ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")])
SCALE = [["Score", "Manual entries per 100 scans"], ["5", "0"], ["4", "1"], ["3", "2–3"], ["2", "4–10"], ["1", "More than 10"]]


def header(title, fields):
    cells = [[Paragraph(f"<b>{name}:</b> " + "_" * 16, small) for name in fields[i:i + 4]] for i in range(0, len(fields), 4)]
    return [Paragraph(title, styles["Heading2"]), Table(cells, colWidths=[2.45 * inch] * 4), Spacer(1, 6)]


story = []
for run in range(1, RUNS + 1):
    name = "UL site-visit run" if run == RUNS and RUNS > 5 else f"Run {run}"
    story += header(f"Test 1 + 4 run sheet — {name}", ["Date", "Scanner (person)", "Observer", "Station / room",
                                                       "Start time", "End time", "Report run #", "Mode: Stack · IN"])
    story.append(Paragraph("Before the run: <b>scripts/test1-scan-reliability.py reset</b>. Present each stack once, in order, as fast as "
                           "comfortable. Tick <b>Rescan</b> for every extra scan a stack needed and <b>&gt; 5 s</b> if it was not "
                           "confirmed (green) within 5 s of being presented. Afterwards run <b>report N</b> and copy its numbers below.", small))
    story.append(Spacer(1, 6))
    rows = [["#", "Label code", "Project", "Test · treatment · pieces", "Green 1st try", "Rescans (tally)", "> 5 s", "Notes"]]
    rows += [[row["label"], row["label_code"], row["project_number"],
              f"{row['test_code']} · {row['conditioning']} · {row['specimens']}", "", "", "", ""] for row in labels]
    table = Table(rows, colWidths=[.35 * inch, .95 * inch, 1.15 * inch, 1.75 * inch, .9 * inch, 1.1 * inch, .55 * inch, 3.05 * inch],
                  rowHeights=[.26 * inch] + [.215 * inch] * len(labels), repeatRows=1)
    table.setStyle(GRID)
    story.append(table)
    story.append(Spacer(1, 6))
    totals = [[Paragraph(f"<b>{text}</b>", small) for text in ["Rescans", "Misses (&gt; 5 s)", "Manual entries", "Scans recorded (report)",
                                                                 "Mean s / scan (report)", "Gaps &gt; 5 s (report)", "UDS score"]],
              ["", "", "", "     / 20", "", "", ""]]
    summary = Table([[Table(totals, colWidths=[.95 * inch] * 7, rowHeights=[.36 * inch, .32 * inch], style=GRID),
                      Table(SCALE, colWidths=[.45 * inch, 1.6 * inch], rowHeights=.15 * inch,
                            style=TableStyle([*GRID.getCommands(), ("FONTSIZE", (0, 0), (-1, -1), 7)]))]],
                    colWidths=[6.9 * inch, 2.4 * inch])
    story += [summary, PageBreak()]

# Test 5: one reference label per reset, taken from the new-sample stacks (each scanned IN once).
references = []
try:
    request = urllib.request.urlopen("http://127.0.0.1:8120/api/stacks?q=4792013", timeout=10)
    for stack in sorted(json.loads(request.read())["items"], key=lambda item: item["id"]):
        references += [(stack["material_ref"], specimen["number"], len(stack["specimens"]), specimen["barcode_value"])
                       for specimen in stack["specimens"]]
except OSError:
    pass
story += header("Test 5 reset log — outage resilience", ["Date", "Tester", "Station IP", "Server (Mac) IP"])
story.append(Paragraph("For each reset: unplug the Pi's power <b>and</b> turn off its network, wait 10 s, restore both, and start the "
                       "stopwatch at reconnection. Stop when the kiosk shows the scan-ready screen with <b>Online</b>. Then scan that "
                       "row's reference label in <b>Single</b> mode (each label is one piece, so every reset records something new) and confirm it turns green. Pass: mean &lt; 60 s, no keyboard/mouse, every scan recorded.", small))
story.append(Spacer(1, 6))
rows = [[Paragraph(f"<b>{text}</b>", small) for text in ["Reset", "Reference label (code · item)", "Recovery time (s)",
                                                          "Keyboard / mouse needed?", "Scan recorded (green)?", "Notes"]]]
for index in range(10):
    ref = references[index] if index < len(references) else None
    rows.append([str(index + 1), f"{ref[3]} · {ref[0][:26]} {ref[1]}/{ref[2]}" if ref else "", "", "yes  /  no", "yes  /  no", ""])
rows.append(["Mean", "", "", "", "", ""])
table = Table(rows, colWidths=[.6 * inch, 3.2 * inch, 1.3 * inch, 1.5 * inch, 1.5 * inch, 1.7 * inch],
              rowHeights=[.3 * inch] + [.36 * inch] * 11)
table.setStyle(GRID)
story.append(table)

OUT.mkdir(parents=True, exist_ok=True)
SimpleDocTemplate(str(OUT / "run-sheets.pdf"), pagesize=landscape(letter), leftMargin=.5 * inch, rightMargin=.5 * inch,
                  topMargin=.45 * inch, bottomMargin=.4 * inch, title="Test run sheets").build(story)
print("Wrote", OUT / "run-sheets.pdf")
