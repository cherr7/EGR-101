#!/usr/bin/env python3
"""Lay out the Test 1 labels for a Brother QL on 62 mm continuous tape, one 62 x 25.4 mm label per page.

Reads output/test-labels/test-labels.csv (written by seed-test-labels.py; registers nothing new).
Usage: .venv/bin/python scripts/test-labels-ql.py  ->  output/test-labels/test-labels-62x25.pdf
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.barcodes import datamatrix_image  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

WIDTH, HEIGHT = 62 * mm, 25.4 * mm      # DK 62 mm continuous tape, cut every 1 inch
CODE = 19 * mm                          # Data Matrix side, as on the production stack label
OUT = ROOT / "output" / "test-labels"

rows = list(csv.DictReader(open(OUT / "test-labels.csv")))
pdf = canvas.Canvas(str(OUT / "test-labels-62x25.pdf"), pagesize=(WIDTH, HEIGHT))
for row in rows:
    pdf.drawImage(ImageReader(datamatrix_image(row["payload"])), 3 * mm, (HEIGHT - CODE) / 2, CODE, CODE)
    x = 3 * mm + CODE + 3 * mm
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(x, HEIGHT - 8 * mm, f"#{int(row['label']):02d}  TEST 1")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(x, HEIGHT - 13 * mm, row["project_number"])
    pdf.drawString(x, HEIGHT - 17 * mm, f"{row['test_code']} · {row['conditioning']}")
    pdf.drawString(x, HEIGHT - 21 * mm, f"{row['specimens']} specimen{'' if row['specimens'] == '1' else 's'}")
    pdf.showPage()
pdf.save()
print(f"Wrote {len(rows)} labels to {OUT / 'test-labels-62x25.pdf'}")
