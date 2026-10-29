#!/usr/bin/env python3
"""Test 1 (scan reliability) and Test 4 (ease of use) helper for the 20 Test 1 stacks.

    baseline   save a backup while every Test 1 stack is still WAITING (once, before the first run)
    reset      restore that backup so the same 20 labels can be scanned IN again (before each run)
    report N   score run N: time per scan from the database's commit timestamps, every record checked
               against output/test-labels/test-labels.csv, rescans/rejections, and gaps over 5 s

Usage: .venv/bin/python scripts/test1-scan-reliability.py baseline|reset|report N
Writes output/test1/run-N.csv and appends to output/test1/summary.json.
"""
import csv
import importlib.util
import json
import statistics
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8120/api"
OUT = ROOT / "output" / "test1"
STATE = OUT / "state.json"
LABELS = list(csv.DictReader(open(ROOT / "output" / "test-labels" / "test-labels.csv")))


def call(method, path, body=None, headers=None, raw=False):
    request = urllib.request.Request(API + path, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=120) as response:
        content = response.read()
    return content if raw else json.loads(content)


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(value):
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(value, indent=2))


def database():
    spec = importlib.util.spec_from_file_location("host", ROOT / "deploy/macos/host.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    import psycopg
    return psycopg.connect(host.environment(host.config()["db_name"])["DATABASE_URL"].replace("+psycopg", ""))


command = sys.argv[1] if len(sys.argv) > 1 else ""
if command == "baseline":
    waiting = [call("GET", f"/stacks/{row['stack_id']}")["waiting_count"] == int(row["specimens"]) for row in LABELS]
    assert all(waiting), "Every Test 1 stack must be WAITING before taking the baseline."
    save({**state(), "baseline": call("POST", "/backups")["id"]})
    print("Baseline saved. Run `reset` before each scan run.")

elif command == "reset":
    archive = call("GET", f"/backups/{state()['baseline']}/download", raw=True)
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"baseline.zip\"\r\n"
            "Content-Type: application/zip\r\n\r\n").encode() + archive + f"\r\n--{boundary}--\r\n".encode()
    staged = call("POST", "/backups/import", body, {"Content-Type": "multipart/form-data; boundary=" + boundary})
    call("POST", f"/backups/imports/{staged['id']}/restore", json.dumps({"confirmation": "RESTORE"}).encode(),
         {"Content-Type": "application/json"})
    save({**state(), "run_started": datetime.now(timezone.utc).isoformat()})
    print("All 20 Test 1 stacks are WAITING again. Scan them IN now, then run `report N`.")

elif command == "report":
    run = int(sys.argv[2])
    since = state()["run_started"]
    expected = {int(row["stack_id"]): row for row in LABELS}
    with database() as db:
        events = db.execute("""
            SELECT e.stack_id, e.direction, e.recorded_at, jsonb_array_length(e.specimen_ids), p.project_number,
                   s.test_code, s.preconditioning_code, e.actor
            FROM audit_events e JOIN stacks s ON s.id = e.stack_id JOIN projects p ON p.id = s.project_id
            WHERE e.kind = 'movement' AND e.recorded_at >= %s AND e.stack_id = ANY(%s)
            ORDER BY e.recorded_at""", (since, list(expected))).fetchall()
        requests = dict(db.execute("""SELECT result->>'status', count(*) FROM movement_requests
                                      WHERE recorded_at >= %s GROUP BY 1""", (since,)).fetchall())
    rows, seen, previous = [], set(), None
    for stack_id, direction, at, count, project, test, conditioning, station in events:
        want = expected[stack_id]
        match = (project == want["project_number"] and test == want["test_code"] and conditioning == want["conditioning"]
                 and count == int(want["specimens"]) and direction == "IN")
        gap = (at - previous).total_seconds() if previous else None
        rows.append({"label": want["label"], "stack_id": stack_id, "label_code": want["label_code"], "recorded_at": at.isoformat(),
                     "seconds_since_previous": round(gap, 3) if gap is not None else "", "over_5_s": bool(gap and gap > 5),
                     "direction": direction, "specimens": count, "station": station, "matches_prep_sheet": match,
                     "duplicate": stack_id in seen})
        seen.add(stack_id); previous = at
    gaps = [r["seconds_since_previous"] for r in rows if r["seconds_since_previous"] != ""]
    mean = statistics.mean(gaps) if gaps else None
    missing = sorted(set(expected) - seen)
    passed = bool(mean is not None and mean < 2.0 and not missing and all(r["matches_prep_sheet"] for r in rows))
    summary = {"run": run, "started": since, "scans_recorded": len(rows), "stacks_missing": missing,
               "mean_seconds_per_scan": round(mean, 3) if mean else None,
               "max_gap_seconds": max(gaps) if gaps else None,
               "gaps_over_5_s": sum(r["over_5_s"] for r in rows),
               "all_records_match_prep_sheet": all(r["matches_prep_sheet"] for r in rows),
               "requests_by_status": requests, "pass": passed}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"run-{run}.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["label"])
        writer.writeheader(); writer.writerows(rows)
    history = json.loads((OUT / "summary.json").read_text()) if (OUT / "summary.json").exists() else []
    history = [item for item in history if item["run"] != run] + [summary]
    (OUT / "summary.json").write_text(json.dumps(history, indent=2))
    print(json.dumps(summary, indent=2))
    if len(history) > 1:
        all_gaps = [summary["mean_seconds_per_scan"] for summary in history if summary["mean_seconds_per_scan"]]
        print(f"{len(history)} runs, {sum(s['scans_recorded'] for s in history)} scans, "
              f"mean of run means {statistics.mean(all_gaps):.2f} s")
else:
    print(__doc__)
