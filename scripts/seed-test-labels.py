#!/usr/bin/env python3
"""Register the 20 Test 1 stacks through the running API and print one sheet of their stack labels.

Usage: .venv/bin/python scripts/seed-test-labels.py [API origin, default http://127.0.0.1:8120]
Writes output/test-labels/test-labels.pdf and test-labels.csv (stack ID, payload, prep fields).
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.barcodes import datamatrix_image, id_payload  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8120").rstrip("/")
OUT = ROOT / "output" / "test-labels"
CONDITIONING = ["AR", "5L", "50L"]
TESTS = ["D5034", "D3786", "D4966", "D3884"]
SIZES = [1, 20, 10, 5, 15, 2, 20, 8, 12, 3, 18, 6, 10, 4, 16, 1, 20, 7, 14, 9]   # 1-20 specimens, both extremes repeated


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


rows = [{"project_number": f"4900{index:08d}", "material_ref": f"TEST-1 fabric {index:02d}",
         "test_code": TESTS[index % len(TESTS)], "preconditioning_code": CONDITIONING[index % len(CONDITIONING)],
         "specimen_count": count} for index, count in enumerate(SIZES, start=1)]
draft = call("POST", "/api/prep-drafts", {"rows": rows})
stack_ids = call("POST", f"/api/prep-drafts/{draft['id']}/commit", {})["stack_ids"]

OUT.mkdir(parents=True, exist_ok=True)
pdf = canvas.Canvas(str(OUT / "test-labels.pdf"), pagesize=letter)
cell_w, cell_h, left, top = 50 * mm, 52 * mm, 12 * mm, letter[1] - 12 * mm
with open(OUT / "test-labels.csv", "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["label", "stack_id", "payload", "project_number", "test_code", "conditioning", "specimens"])
    for index, (stack_id, row) in enumerate(zip(stack_ids, rows)):
        payload = id_payload(stack_id)
        writer.writerow([index + 1, stack_id, payload, row["project_number"], row["test_code"],
                         row["preconditioning_code"], row["specimen_count"]])
        x, y = left + (index % 4) * cell_w, top - (index // 4 + 1) * cell_h
        pdf.drawImage(ImageReader(datamatrix_image(payload)), x + 8 * mm, y + 14 * mm, 30 * mm, 30 * mm)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(x + 2 * mm, y + 9 * mm, f"#{index + 1:02d}  {row['project_number']}")
        pdf.setFont("Helvetica", 7)
        pdf.drawString(x + 2 * mm, y + 5 * mm, f"{row['test_code']} · {row['preconditioning_code']} · {row['specimen_count']} specimens")
pdf.save()
print(f"Registered {len(stack_ids)} stacks; labels in {OUT / 'test-labels.pdf'}")
