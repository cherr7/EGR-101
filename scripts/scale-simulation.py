#!/usr/bin/env python3
"""Simulate months of lab traffic in a throwaway database and measure size and API latency as it grows.

Uses the app's own registration (register_stack) and movement (process_movement) code on a simulated clock,
against <db_name>_scale on the local host's PostgreSQL. The live database is never touched.

Usage (from the repo root, with the local host running):
    .venv/bin/python scripts/scale-simulation.py DAYS [SPECIMENS_PER_ROOM_PER_DAY] [--extend-to TOTAL_DAYS]
    .venv/bin/python scripts/scale-simulation.py --drop   # delete the scratch database
--extend-to copies the simulated days forward (same row shapes, shifted IDs and times) until TOTAL_DAYS,
then simulates one more real day at that size, so a 5-year database can be measured in minutes.
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

args = sys.argv[1:]
EXTEND_TO = int(args.pop(args.index("--extend-to") + 1)) if "--extend-to" in args else None
args = [a for a in args if a != "--extend-to"]
DAYS = int(args[0])
PER_ROOM = int(args[1]) if len(args) > 1 else 1000
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
from app.models import AuditEvent, ConditioningSession, Stack  # noqa: E402
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


def simulate_day(day):
    """Register PER_ROOM specimens per room at 07:00, scan them IN during the day, and yesterday's OUT."""
    global waiting, recent_codes, total_specimens
    base = start + timedelta(days=day - 1)
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
        stack_ids = [services.register_stack(db, row).id for row in rows]
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
        with SessionLocal() as db, db.begin():
            number = services.reserve_request_numbers(db, station, 1)["first"]
        body = MovementBody(request_id=f"{station}-{number}", station_id=station, code=label_code(first[sid]), direction=direction)
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
    return len(actions)


def checkpoint(day, moves):
    psql("VACUUM ANALYZE")
    size = int(psql("SELECT pg_database_size(current_database())"))
    tables = dict(line.split("|") for line in psql(
        "SELECT relname, pg_total_relation_size(relid) FROM pg_stat_user_tables ORDER BY 2 DESC LIMIT 6").splitlines())
    oldest = psql("SELECT min(id) FROM specimens")
    latency = {
        "scan lookup": timed("lookup", lambda: client.post("/api/lookup", json={"code": rng.choice(recent_codes)})),
        "oldest record lookup": timed("oldest", lambda: client.post("/api/lookup", json={"code": label_code(int(oldest))})),
        "kiosk activity poll": timed("events", lambda: client.get("/api/events?limit=40&kind=movement&room_id=shared")),
        "staff transactions": timed("transactions", lambda: client.get("/api/events?limit=100")),
        "kiosk inventory": timed("inventory", lambda: client.get("/api/stacks?room_id=shared"), runs=3),
        "staff stack search": timed("search", lambda: client.get("/api/stacks?q=Material%20123"), runs=3),
        "staff stack list (all)": timed("all", lambda: client.get("/api/stacks"), runs=1),
    }
    day_moves = movement_ms[-moves:]
    point = {"day": day, "specimens": int(psql("SELECT count(*) FROM specimens")),
             "events": int(psql("SELECT count(*) FROM audit_events")),
             "db_mb": round(size / 2**20, 1), "tables_mb": {k: round(int(v) / 2**20, 1) for k, v in tables.items()},
             "movements_timed": len(day_moves),
             "movement_ms_p50": round(statistics.median(day_moves), 1),
             "movement_ms_p95": round(sorted(day_moves)[int(len(day_moves) * .95)], 1),
             "movement_ms_max": round(max(day_moves), 1),
             "latency_ms": latency, "elapsed_min": round((time.time() - began) / 60, 1)}
    results.append(point)
    print(json.dumps(point), flush=True)
    (ROOT / "output/scale").mkdir(parents=True, exist_ok=True)
    (ROOT / "output/scale/results.json").write_text(json.dumps(results, indent=2))


def extend(total_days):
    """Copy the simulated days forward until total_days: the same rows with shifted IDs and timestamps."""
    stacks, specimens = int(psql("SELECT max(id) FROM stacks")), int(psql("SELECT max(id) FROM specimens"))
    requests = int(psql("SELECT coalesce(max(number), 0) FROM movement_requests"))
    psql("CREATE TABLE base_projects AS SELECT id FROM projects")
    for k in range(1, -(-total_days // DAYS)):
        shift = f"interval '{k * DAYS} days'"
        psql(f"""
            INSERT INTO projects (project_number) SELECT project_number || '-{k}'
              FROM projects WHERE id IN (SELECT id FROM base_projects);
            INSERT INTO stacks (id, project_id, material_id, test_code, preconditioning_code, source_metadata, version, is_demo, created_at)
              SELECT id + {k * stacks}, project_id, material_id, test_code, preconditioning_code, source_metadata, version, is_demo, created_at + {shift}
              FROM stacks WHERE id <= {stacks};
            INSERT INTO specimens (id, stack_id, number, revision)
              SELECT id + {k * specimens}, stack_id + {k * stacks}, number, revision FROM specimens WHERE id <= {specimens};
            INSERT INTO conditioning_sessions (specimen_id, room_id, entered_at, exited_at, recorded_at, source, corrected,
                                               incomplete, early_exit_acknowledged, voided)
              SELECT specimen_id + {k * specimens}, room_id, entered_at + {shift}, exited_at + {shift},
                     recorded_at + {shift}, source, corrected, incomplete, early_exit_acknowledged, voided
              FROM conditioning_sessions WHERE specimen_id <= {specimens};
            INSERT INTO audit_events (stack_id, kind, direction, actor, reason, specimen_ids, room_id, payload, recorded_at)
              SELECT stack_id + {k * stacks}, kind, direction, actor, reason, specimen_ids, room_id,
                     payload, recorded_at + {shift}
              FROM audit_events WHERE stack_id <= {stacks};
            INSERT INTO movement_requests (station_id, number, fingerprint, result, recorded_at)
              SELECT station_id, number + {k * requests}, fingerprint, result, recorded_at + {shift}
              FROM movement_requests WHERE recorded_at <= '{start + timedelta(days=DAYS)}';""")
        print(f"copied through day {(k + 1) * DAYS}", flush=True)
    psql("DROP TABLE base_projects")
    psql(f"UPDATE request_counters SET last_issued = (SELECT max(number) FROM movement_requests m WHERE m.station_id = client)")
    for table in ("stacks", "specimens"):
        psql(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}) + 1, false)")
    return -(-total_days // DAYS) * DAYS


for day in range(1, DAYS + 1):
    moves = simulate_day(day)
    if day in CHECKPOINTS:
        checkpoint(day, moves)
if EXTEND_TO:
    reached = extend(EXTEND_TO)
    slow.add("all")   # the unfiltered staff list would take many minutes at this size
    start += timedelta(days=reached - DAYS)   # continue the clock after the copied days
    moves = simulate_day(DAYS + 1)
    checkpoint(reached + 1, moves)
