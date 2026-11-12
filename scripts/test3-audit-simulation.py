#!/usr/bin/env python3
"""Test 3 (auditability): 90 simulated days of check-ins, partial checkouts, undos, edits and missed scans,
then every field compared with an independent ground-truth log, and the history checked for changed or
deleted rows.

Every write goes through the same HTTP API the staff web interface and kiosks use, on a simulated clock,
in a throwaway database <db_name>_audit (the live database is never touched).

Usage: .venv/bin/python scripts/test3-audit-simulation.py SEED [DAYS] [STACKS_PER_DAY]
Writes output/test3/seed-SEED.json and an exported audit trail sample.
"""
import collections
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from prep_sheet_body import sheet_body

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host", ROOT / "deploy/macos/host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
SEED = int(sys.argv[1])
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 90
PER_DAY = int(sys.argv[3]) if len(sys.argv) > 3 else 40
NAME = f"{host.config()['db_name']}_audit"
PG = host.pg_bin()
OUT = ROOT / "output" / "test3"

subprocess.run([str(PG / "dropdb"), "--if-exists", NAME], env=host.environment("postgres"), check=True)
subprocess.run([str(PG / "createdb"), NAME], env=host.environment("postgres"), check=True)
env = host.environment(NAME)
subprocess.run([str(host.python_bin()), "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env,
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.environ.update(env)
sys.path.insert(0, str(ROOT / "backend"))

import psycopg  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import corrections, main, movements, services  # noqa: E402
from app.models import AuditEvent, ConditioningSession, Stack  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

clock = {"now": None}
for module in (movements, services, main, corrections):
    if hasattr(module, "utcnow"):
        module.utcnow = lambda: clock["now"]


@event.listens_for(Session, "before_flush")
def stamp_simulated_time(session, context, instances):
    for row in session.new:
        if isinstance(row, (AuditEvent, ConditioningSession)) and row.recorded_at is None:
            row.recorded_at = clock["now"]
        elif isinstance(row, Stack) and row.created_at is None:
            row.created_at = clock["now"]


from app.db import SessionLocal  # noqa: E402
with SessionLocal.begin() as db:
    services.seed_config(db)       # rooms, stations and treatments, as on a real install
rng = random.Random(SEED)
raw = psycopg.connect(env["DATABASE_URL"].replace("+psycopg", ""), autocommit=True)
client = TestClient(main.app)
start = datetime(2026, 1, 5, tzinfo=timezone.utc)
ROOMS = {"ppe": "ppe-door", "shared": "shared-door", "footwear": "footwear-door"}
truth_stacks, truth = {}, {}          # stack fields; specimen id -> {"room", "entered", "exited"}
written, counts = {}, collections.Counter()   # event id -> hash of its row as first written


def api(method, path, body=None, expect="committed"):
    response = client.request(method, path, json=body)
    assert response.status_code == 200, (path, response.status_code, response.text[:300])
    value = response.json()
    if expect:
        assert value.get("status") == expect, (path, value)
    return value


def row_hash(event_id):
    row = raw.execute("SELECT row_to_json(e)::text FROM audit_events e WHERE id = %s", (event_id,)).fetchone()
    return hashlib.sha256(row[0].encode()).hexdigest()


def record(kind, value):
    counts[kind] += 1
    written[value["event_id"]] = row_hash(value["event_id"])
    return value


def move(at, stack, direction, room, scope="stack", ids=None):
    clock["now"] = at
    first = api("POST", "/api/request-ids", {"client": ROOMS[room], "count": 1}, expect=None)["first"]
    body = {"request_id": f"{ROOMS[room]}-{first}", "station_id": ROOMS[room], "code": stack["specimens"][0]["barcode_value"],
            "direction": direction, "scope": scope}
    if ids is not None:
        body.update(selected_specimen_ids=ids, expected_version=api("GET", f"/api/stacks/{stack['id']}", expect=None)["version"])
    value = api("POST", "/api/movements", body)
    return record("partial OUT" if ids is not None else f"scan {direction}", value)


def undo(at, value):
    clock["now"] = at
    return record("undo", api("POST", f"/api/transactions/{value['event_id']}/undo", {}))


def edit(at, value, occurred):
    clock["now"] = at
    return record("edit", api("PATCH", f"/api/transactions/{value['event_id']}",
                              {"occurred_at": occurred.isoformat(), "note": "Corrected time from the room log"}))


in_room = []
for day in range(DAYS):
    base = start + timedelta(days=day)
    clock["now"] = base + timedelta(hours=7)
    rows = [{"project_number": f"4792{rng.randrange(10**8):08d}", "material_ref": f"Fabric {rng.randrange(300):03d}",
             "test_code": rng.choice(["Flame Resistance", "Heat and Thermal Shrinkage", "Seam Breaking Strength",
                                      "Thermal Stability", "Water Absorption Resistance", "Thread Melting"]),
             "preconditioning_code": rng.choice(["AR", "5L", "25L", "50L", "100L", "AR → 5L"]),
             "specimen_count": rng.choice([1, 1, 2, 3, 5, 5, 8, 10, 10, 12, 15, 20])} for _ in range(PER_DAY)]
    draft = api("POST", "/api/prep-drafts", sheet_body(rows), expect=None)
    stack_ids = api("POST", f"/api/prep-drafts/{draft['id']}/commit", {}, expect=None)["stack_ids"]
    counts["registration"] += len(stack_ids)
    next_in_room = []
    # Yesterday's stacks leave 24-26 h after entry: some in two parts, some undone or corrected.
    for stack, room, entered in in_room:
        out_at = entered + timedelta(hours=24, seconds=rng.randrange(7200))
        ids = [p["id"] for p in stack["specimens"]]
        if len(ids) > 1 and rng.random() < .3:
            part = rng.sample(ids, rng.randrange(1, len(ids)))
            move(out_at, stack, "OUT", room, "selection", part)
            for pid in part:
                truth[pid]["exited"] = out_at
            out_at += timedelta(minutes=30)
        remaining = [pid for pid in ids if truth[pid]["exited"] is None]
        value = move(out_at, stack, "OUT", room)
        roll = rng.random()
        if roll < .15:      # wrong scan: undo it, then scan again
            undo(out_at + timedelta(minutes=1), value)
            out_at += timedelta(minutes=2)
            move(out_at, stack, "OUT", room)
        elif roll < .30:    # staff correct the recorded time
            out_at -= timedelta(minutes=5)
            edit(out_at + timedelta(minutes=6), value, out_at)
        for pid in remaining:
            truth[pid]["exited"] = out_at
    # Today's stacks go in between 08:00 and 17:00.
    for stack_id, row in zip(stack_ids, rows):
        stack = api("GET", f"/api/stacks/{stack_id}", expect=None)
        truth_stacks[stack_id] = row
        room = rng.choice(list(ROOMS))
        at = base + timedelta(hours=8, seconds=rng.randrange(9 * 3600))
        ids = [p["id"] for p in stack["specimens"]]
        roll = rng.random()
        if roll < .05:      # the scan was missed; staff record it afterwards
            clock["now"] = at + timedelta(minutes=45)
            record("missed scan", api("POST", "/api/transactions/missed", {
                "stack_id": stack_id, "specimen_ids": ids, "direction": "IN", "occurred_at": at.isoformat(),
                "room_id": room, "note": "Entered from the paper log"}))
        else:
            value = move(at, stack, "IN", room)
            if roll < .20:
                undo(at + timedelta(minutes=1), value)
                at += timedelta(minutes=2)
                move(at, stack, "IN", room)
            elif roll < .35:
                at -= timedelta(minutes=10)
                edit(at + timedelta(minutes=11), value, at)
        for pid in ids:
            truth[pid] = {"room": room, "entered": at, "exited": None}
        next_in_room.append((stack, room, at))
    in_room = next_in_room
    if day % 10 == 9:
        print(f"seed {SEED}: day {day + 1}, {sum(counts.values())} records", flush=True)

# --- verification ---------------------------------------------------------------------------------
clock["now"] = start + timedelta(days=DAYS + 2)
mismatches, fields_checked = [], 0
parse = lambda value: datetime.fromisoformat(value) if value else None  # noqa: E731
for stack_id, row in truth_stacks.items():
    detail = api("GET", f"/api/stacks/{stack_id}", expect=None)
    for field in ("project_number", "material_ref", "test_code", "preconditioning_code"):
        fields_checked += 1
        if detail[field] != row[field]:
            mismatches.append((stack_id, field, detail[field], row[field]))
    fields_checked += 1
    if detail["specimen_count"] != row["specimen_count"]:
        mismatches.append((stack_id, "specimen_count", detail["specimen_count"], row["specimen_count"]))
    for specimen in detail["specimens"]:
        want = truth[specimen["id"]]
        period = specimen["sessions"][0] if specimen["sessions"] else {}
        got = {"room": period.get("room_id"), "entered": parse(specimen["ENTRY"]), "exited": parse(specimen["EXIT"]),
               "state": specimen["state"]}
        expected = {**want, "state": "IN" if want["exited"] is None else "OUT"}
        for field in expected:
            fields_checked += 1
            if got[field] != expected[field]:
                mismatches.append((specimen["id"], field, str(got[field]), str(expected[field])))

total_events = raw.execute("SELECT count(*) FROM audit_events").fetchone()[0]
changed = [event_id for event_id, digest in written.items() if row_hash(event_id) != digest]
blocked = []
for statement in ("UPDATE audit_events SET reason = 'tampered'", "DELETE FROM audit_events"):
    try:
        raw.execute(statement)
    except psycopg.Error as error:
        blocked.append(str(error).splitlines()[0])
project = next(iter(truth_stacks.values()))["project_number"]
OUT.mkdir(parents=True, exist_ok=True)
(OUT / f"audit-trail-sample-seed-{SEED}.csv").write_bytes(client.get(f"/api/exports/history.csv?project_number={project}").content)
size_mb = raw.execute("SELECT pg_database_size(current_database())").fetchone()[0] / 2**20
result = {"seed": SEED, "days": DAYS, "stacks": len(truth_stacks), "specimens": len(truth),
          "writes_by_type": dict(counts), "fields_checked": fields_checked, "field_mismatches": len(mismatches),
          "mismatch_examples": mismatches[:10],
          "history_rows": total_events, "history_rows_expected": sum(counts.values()),
          "history_rows_changed_after_writing": len(changed),
          "update_and_delete_blocked": len(blocked) == 2, "blocked_messages": blocked, "db_mb": round(size_mb, 1),
          "pass": not mismatches and not changed and total_events == sum(counts.values()) and len(blocked) == 2}
(OUT / f"seed-{SEED}.json").write_text(json.dumps(result, indent=2, default=str))
print(json.dumps(result, indent=2, default=str))
raw.close()
client.close()
subprocess.run([str(PG / "dropdb"), "--force", "--if-exists", NAME], env=host.environment("postgres"), check=True)
