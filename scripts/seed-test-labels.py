#!/usr/bin/env python3
"""Register the 20 Test 1 stacks through the running API and save their labels for the Brother QL.

Each stack gets one label, for piece 1: in Stack mode it moves the whole stack, in Single mode
only that piece. The PDF comes from the app's own label preview (62 x 25.4 mm pages).

Usage: .venv/bin/python scripts/seed-test-labels.py [API origin, default http://127.0.0.1:8120]
Writes output/test-labels/test-labels.csv and test-labels.pdf. Print with:
    lp -d Brother_QL_810W -o media=Custom.62x25.4mm -o CutMedia=EndOfPage output/test-labels/test-labels.pdf
"""
import csv
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8120").rstrip("/")
OUT = ROOT / "output" / "test-labels"
CONDITIONING = ["AR", "5L", "50L"]
TESTS = ["D5034", "D3786", "D4966", "D3884"]
SIZES = [1, 20, 10, 5, 15, 2, 20, 8, 12, 3, 18, 6, 10, 4, 16, 1, 20, 7, 14, 9]   # 1-20 specimens, both extremes repeated


def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()
        return content if response.headers.get_content_type() == "application/pdf" else json.loads(content)


rows = [{"project_number": f"4900{index:08d}", "material_ref": f"TEST-1 fabric {index:02d}",
         "test_code": TESTS[index % len(TESTS)], "preconditioning_code": CONDITIONING[index % len(CONDITIONING)],
         "specimen_count": count} for index, count in enumerate(SIZES, start=1)]
draft = call("POST", "/api/prep-drafts", {"rows": rows})
stack_ids = call("POST", f"/api/prep-drafts/{draft['id']}/commit", {})["stack_ids"]
first = [call("GET", f"/api/stacks/{stack_id}")["specimens"][0] for stack_id in stack_ids]

OUT.mkdir(parents=True, exist_ok=True)
with open(OUT / "test-labels.csv", "w", newline="") as handle:
    writer = csv.writer(handle)
    writer.writerow(["label", "stack_id", "label_code", "project_number", "test_code", "conditioning", "specimens"])
    for index, (stack_id, specimen, row) in enumerate(zip(stack_ids, first, rows), start=1):
        writer.writerow([index, stack_id, specimen["barcode_value"], row["project_number"], row["test_code"],
                         row["preconditioning_code"], row["specimen_count"]])
pdf = call("POST", "/api/labels/preview", {"stack_ids": stack_ids, "selected_specimen_ids": [p["id"] for p in first]})
(OUT / "test-labels.pdf").write_bytes(pdf)
print(f"Registered {len(stack_ids)} stacks; labels in {OUT / 'test-labels.pdf'}")
