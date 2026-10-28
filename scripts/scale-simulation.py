#!/usr/bin/env python3
"""Simulate months of lab traffic in a throwaway database and measure size and API latency as it grows.

Uses the app's own registration (commit_draft) and movement (process_movement) code on a simulated clock,
against <db_name>_scale on the local host's PostgreSQL. The live database is never touched.

Usage (from the repo root, with the local host running):
    .venv/bin/python scripts/scale-simulation.py DAYS [SPECIMENS_PER_ROOM_PER_DAY]
    .venv/bin/python scripts/scale-simulation.py --drop   # delete the scratch database
Writes output/scale/results.json and prints one line per checkpoint.
"""
import importlib.util
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("host", ROOT / "deploy/macos/host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
NAME = host.config()["db_name"] + "_scale"
PG = host.pg_bin()


def psql(sql, database=NAME):
    return subprocess.check_output([str(PG / "psql"), "-At", "-c", sql], env=host.environment(database), text=True).strip()


if sys.argv[1:] == ["--drop"]:
    subprocess.run([str(PG / "dropdb"), "--if-exists", NAME], env=host.environment("postgres"), check=True)
    print("Dropped", NAME)
    sys.exit()

DAYS = int(sys.argv[1])
PER_ROOM = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
ROOMS = [("ppe", "ppe-door"), ("shared", "shared-door"), ("footwear", "footwear-door")]
SIZES = list(range(1, 21))
WEIGHTS = [8, 4, 4, 4, 9, 3, 2, 3, 2, 9, 1, 1, 1, 1, 3, 1, 1, 1, 1, 6]      # singles, 5s, 10s and 20s are common
CHECKPOINTS = sorted({1, 7, 14, 30, 60, 90, 120, 180, 270, 365, DAYS} & set(range(1, DAYS + 1)))

subprocess.run([str(PG / "dropdb"), "--if-exists", NAME], env=host.environment("postgres"), check=True)
subprocess.run([str(PG / "createdb"), NAME], env=host.environment("postgres"), check=True)
env = host.environment(NAME)
subprocess.run([str(host.python_bin()), "-m", "alembic", "upgrade", "head"], cwd=ROOT / "backend", env=env,
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
os.environ.update(env)
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from app import main, movements, services  # noqa: E402
from app.barcodes import label_code  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AuditEvent, ConditioningSession, PrepDraft, Stack  # noqa: E402
from app.schemas import MovementBody  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

clock = {"now": None}
simulated = lambda: clock["now"]  # noqa: E731
for module in (movements, services, main):
    if hasattr(module, "utcnow"):
        module.utcnow = simulated


@event.listens_for(Session, "before_flush")
def stamp_simulated_time(session, context, instances):
    # Column defaults captured the real clock; audit rows are append-only, so stamp them before insert.
    for row in session.new:
        if isinstance(row, AuditEvent) and row.recorded_at is None:
            row.recorded_at = clock["now"]
        elif isinstance(row, Stack) and row.created_at is None:
            row.created_at = clock["now"]
        elif isinstance(row, ConditioningSession) and row.recorded_at is None:
            row.recorded_at = clock["now"]

rng = random.Random(7)
start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAYS)
with SessionLocal.begin() as db:
    services.seed_config(db)       # rooms, stations and treatments, as on a real install
client = TestClient(main.app)
results, waiting, slow, first = [], [], set(), {}
movement_ms, recent_codes, total_specimens = [], [], 0
began = time.time()


def timed(label, call, runs=7):
    if label in slow:                     # already beyond 20 s: skip so the run finishes
        return None
    times = []
    for _ in range(runs):
        t = time.perf_counter(); response = call(); times.append((time.perf_counter() - t) * 1000)
        assert response.status_code == 200, (label, response.status_code, response.text[:200])
        if times[-1] > 20000:
            slow.add(label); break
    return round(statistics.median(times), 1)


for day in range(1, DAYS + 1):
    base = start + timedelta(days=day - 1)
    # 07:00 prep registers the day's stacks: PER_ROOM specimens for each room.
    clock["now"] = base + timedelta(hours=7)
    rows, rooms = [], []
    for room, station in ROOMS:
        left = PER_ROOM
        while left:
            size = min(left, rng.choices(SIZES, WEIGHTS)[0]); left -= size
            rows.append({"project_number": f"4792{rng.randrange(10**8):08d}", "material_ref": f"Material {rng.randrange(400):03d}",
                         "test_code": rng.choice(["ASTM-D5034", "ASTM-D6413", "ASTM-D3786", "ASTM-F1959", "ISO-20344"]),
                         "preconditioning_code": rng.choice(["AR", "5L", "25L", "50L"]), "specimen_count": size})
            rooms.append(station)
    with SessionLocal() as db, db.begin():
        draft = PrepDraft(rows=rows); db.add(draft); db.flush()
        stack_ids = services.commit_draft(db, draft)
        first.update(db.execute(services.select(services.Specimen.stack_id, services.Specimen.id)
                                .where(services.Specimen.stack_id.in_(stack_ids), services.Specimen.number == 1)).all())
    total_specimens += sum(r["specimen_count"] for r in rows)
    # IN between 08:00 and 17:00; yesterday's stacks go OUT 24-26 h after they went in.
    actions = [(base + timedelta(hours=8, seconds=rng.randrange(9 * 3600)), "IN", sid, station)
               for sid, station in zip(stack_ids, rooms)]
    actions += [(entered + timedelta(hours=24, seconds=rng.randrange(2 * 3600)), "OUT", sid, station)
                for entered, sid, station in waiting]
    waiting = []
    for at, direction, sid, station in sorted(actions, key=lambda item: item[0]):
        clock["now"] = at
        body = MovementBody(request_id=uuid.uuid4(), station_id=station, code=label_code(first[sid]), direction=direction)
        t = time.perf_counter()
        with SessionLocal() as db, db.begin():
            result = movements.process_movement(db, body)
        movement_ms.append((time.perf_counter() - t) * 1000)
        assert result["status"] == "committed", result
        if direction == "IN":
            waiting.append((at, sid, station))
        else:
            first.pop(sid)
    recent_codes = [label_code(first[sid]) for sid in stack_ids[-50:]]
    if day in CHECKPOINTS:
        psql("VACUUM ANALYZE")
        size = int(psql("SELECT pg_database_size(current_database())"))
        tables = dict(line.split("|") for line in psql(
            "SELECT relname, pg_total_relation_size(relid) FROM pg_stat_user_tables ORDER BY 2 DESC LIMIT 6").splitlines())
        latency = {
            "scan lookup": timed("lookup", lambda: client.post("/api/lookup", json={"code": rng.choice(recent_codes)})),
            "kiosk activity poll": timed("events", lambda: client.get("/api/events?limit=40&kind=movement&room_id=shared")),
            "staff transactions": timed("transactions", lambda: client.get("/api/events?limit=100")),
            "kiosk inventory": timed("inventory", lambda: client.get("/api/stacks?room_id=shared"), runs=3),
            "staff stack search": timed("search", lambda: client.get("/api/stacks?q=Material%20123"), runs=3),
            "staff stack list (all)": timed("all", lambda: client.get("/api/stacks"), runs=1),
        }
        day_moves = movement_ms[-len(actions):]
        point = {"day": day, "specimens": total_specimens, "events": int(psql("SELECT count(*) FROM audit_events")),
                 "db_mb": round(size / 2**20, 1), "tables_mb": {k: round(int(v) / 2**20, 1) for k, v in tables.items()},
                 "movement_ms_p50": round(statistics.median(day_moves), 1),
                 "movement_ms_p95": round(sorted(day_moves)[int(len(day_moves) * .95)], 1),
                 "latency_ms": latency, "elapsed_min": round((time.time() - began) / 60, 1)}
        results.append(point)
        print(json.dumps(point), flush=True)
        (ROOT / "output/scale").mkdir(parents=True, exist_ok=True)
        (ROOT / "output/scale/results.json").write_text(json.dumps(results, indent=2))
