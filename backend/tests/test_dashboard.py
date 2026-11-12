from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import update


def move(client, stack, direction="IN", station="shared-door", **extra):
    body = {"request_id": str(uuid4()), "station_id": station, "code": stack["specimens"][0]["barcode_value"],
            "direction": direction, "scope": "stack", **extra}
    response = client.post("/api/movements", json=body)
    assert response.is_success, response.text
    assert response.json()["status"] == "committed", response.text
    return response.json()


def age_sessions(db, stack, hours):
    """Pretend the stack's pieces entered `hours` ago (sessions only; audit rows are append-only)."""
    from app.models import ConditioningSession
    ids = [s["id"] for s in stack["specimens"]]
    db.execute(update(ConditioningSession).where(ConditioningSession.specimen_id.in_(ids))
               .values(entered_at=datetime.now(timezone.utc) - timedelta(hours=hours)))
    db.commit()


def dash(client, **params):
    response = client.get("/api/dashboard", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(rows):
    return [row["id"] for row in rows]


def test_new_stack_is_a_label_gap_until_confirmed(client, stack_factory):
    stack = stack_factory(count=4)
    data = dash(client)
    assert ids(data["gaps"]["needs_labels"]) == [stack["id"]]
    assert data["gaps"]["needs_labels"][0]["unlabeled_waiting"] == 4
    assert data["gaps"]["needs_labels"][0]["batch"]["id"] == stack["draft_id"]
    assert data["gaps"]["not_scanned_in"] == []
    confirmed = client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]]})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["label_count"] == 4
    data = dash(client)
    assert data["gaps"]["needs_labels"] == []
    assert ids(data["gaps"]["not_scanned_in"]) == [stack["id"]]
    assert data["gaps"]["not_scanned_in"][0]["labeled_at"]
    history = client.get("/api/transactions", params={"stack_id": stack["id"]}).json()["items"]
    assert any(e["kind"] == "label_print" and e["payload"]["status"] == "manual" for e in history)


def test_label_confirm_is_idempotent_and_validates_stacks(client, stack_factory):
    stack = stack_factory(count=2)
    request_id = str(uuid4())
    first = client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]], "request_id": request_id}).json()
    again = client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]], "request_id": request_id}).json()
    assert first["request_id"] == again["request_id"] == request_id
    events = client.get("/api/transactions", params={"stack_id": stack["id"]}).json()["items"]
    assert sum(e["kind"] == "label_print" for e in events) == 1
    assert client.post("/api/labels/confirm", json={"stack_ids": [999999]}).status_code == 404


def test_scanning_in_implies_labels_and_clears_both_gaps(client, stack_factory):
    stack = stack_factory(count=3)
    move(client, stack, "IN")
    data = dash(client)
    assert data["gaps"]["needs_labels"] == [] and data["gaps"]["not_scanned_in"] == []
    shared = next(room for room in data["rooms"] if room["id"] == "shared")
    assert shared["inside"] == 3 and shared["ready"] == 0


def test_ready_groups_by_room_longest_waiting_first_with_pileup_buckets(client, db, stack_factory):
    fresh = stack_factory(count=2, project="P-FRESH")
    old = stack_factory(count=3, project="P-OLD")
    waiting = stack_factory(count=1, project="P-WAIT")
    for stack in (fresh, old, waiting):
        move(client, stack, "IN")
    age_sessions(db, fresh, 25)          # ready for ~1 h
    age_sessions(db, old, 24 + 24 * 4)   # ready for ~4 days -> pile-up
    data = dash(client)
    shared = next(room for room in data["ready"] if room["room_id"] == "shared")
    assert ids(shared["items"]) == [old["id"], fresh["id"]]  # waiting stack is not ready yet
    assert shared["pieces"] == 5 and shared["stacks"] == 2
    assert shared["buckets"] == {"under_1d": 2, "1_to_3d": 0, "over_3d": 3}
    assert shared["items"][0]["age_bucket"] == "over_3d"
    assert shared["oldest_ready_since"] == shared["items"][0]["ready_since"]
    assert next(room for room in data["rooms"] if room["id"] == "shared")["ready"] == 5


def test_dashboard_ready_matches_stack_detail(client, db, stack_factory):
    stack = stack_factory(count=2)
    move(client, stack, "IN")
    age_sessions(db, stack, 30)
    detail = client.get(f'/api/stacks/{stack["id"]}').json()
    row = next(room for room in dash(client)["ready"] if room["room_id"] == "shared")["items"][0]
    assert row["room_ready"] == sum(s["ready"] for s in detail["specimens"]) == 2
    earliest_due = min(datetime.fromisoformat(s["due_at"]) for s in detail["specimens"])
    assert datetime.fromisoformat(row["ready_since"]) == earliest_due


def test_filters_narrow_every_section(client, db, stack_factory):
    a = stack_factory(count=1, project="P-A", test="T-1", treatment="AR")
    b = stack_factory(count=1, project="P-B", test="T-2", treatment="5L")
    c = stack_factory(count=1, project="P-C", test="T-1")
    move(client, b, "IN", station="ppe-door")
    age_sessions(db, b, 30)
    assert ids(dash(client, project_number="P-A")["gaps"]["needs_labels"]) == [a["id"]]
    assert ids(dash(client, test_code="T-1")["gaps"]["needs_labels"]) == [a["id"], c["id"]]
    assert dash(client, preconditioning_code="5L")["gaps"]["needs_labels"] == []
    assert ids(dash(client, batch_id=c["draft_id"])["gaps"]["needs_labels"]) == [c["id"]]
    assert ids(dash(client, q="p-c")["gaps"]["needs_labels"]) == [c["id"]]
    by_room = dash(client, room_id="ppe")
    assert [room["room_id"] for room in by_room["ready"]] == ["ppe"]
    assert ids(by_room["ready"][0]["items"]) == [b["id"]]
    assert len(by_room["gaps"]["needs_labels"]) == 2  # never-entered stacks have no room yet
    chips = {room["id"]: room for room in dash(client, project_number="P-A")["rooms"]}
    assert chips["ppe"]["inside"] == 0  # P-B's pieces are filtered out of the chip counts
    assert {room["id"]: room for room in dash(client, project_number="P-B")["rooms"]}["ppe"] == {"id": "ppe", "name": chips["ppe"]["name"], "inside": 1, "ready": 1}
    facets = dash(client)["facets"]
    assert facets["projects"] == ["P-A", "P-B", "P-C"] and "5L" in facets["treatments"]
    assert {batch["id"] for batch in facets["batches"]} == {a["draft_id"], b["draft_id"], c["draft_id"]}


def test_batch_stages_progress_and_close(client, db, stack_factory):
    stack = stack_factory(count=2)
    batch = client.get(f'/api/batches/{stack["draft_id"]}').json()
    assert batch["open"] and batch["stages"] == {"total": 2, "labeled": 0, "entered": 0, "inside": 0, "ready": 0, "finished": 0}
    client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]]})
    entered = move(client, stack, "IN")
    age_sessions(db, stack, 26)
    stages = client.get(f'/api/batches/{stack["draft_id"]}').json()["stages"]
    assert stages == {"total": 2, "labeled": 2, "entered": 2, "inside": 2, "ready": 2, "finished": 0}
    move(client, stack, "OUT", expected_version=entered["version"])
    closed = client.get(f'/api/batches/{stack["draft_id"]}').json()
    assert not closed["open"] and closed["stages"]["finished"] == 2
    listed = {b["id"]: b for b in dash(client)["batches"]}
    assert listed[stack["draft_id"]]["open"] is False
    assert client.get("/api/batches/not-a-batch").status_code == 404


def test_transactions_filter_by_batch(client, stack_factory):
    one = stack_factory(count=1, project="P-1")
    two = stack_factory(count=1, project="P-2")
    move(client, one, "IN")
    move(client, two, "IN")
    events = client.get("/api/transactions", params={"batch_id": one["draft_id"]}).json()["items"]
    assert events and {e["stack_id"] for e in events} == {one["id"]}
    assert client.get("/api/transactions", params={"batch_id": "missing"}).status_code == 404


def test_print_job_selection_counts_individual_pieces(client, db, stack_factory):
    from app.models import PrintJob
    stack = stack_factory(count=3)
    first_two = [s["id"] for s in stack["specimens"][:2]]
    db.add(PrintJob(id=str(uuid4()), fingerprint="x", status="submitted", printer_queue="QL", stack_ids=[stack["id"]],
                    options={"selected_specimen_ids": first_two}))
    db.add(PrintJob(id=str(uuid4()), fingerprint="y", status="unknown", printer_queue="QL", stack_ids=[stack["id"]],
                    options={"selected_specimen_ids": [stack["specimens"][2]["id"]]}))
    db.commit()
    data = dash(client)
    gap = data["gaps"]["needs_labels"][0]
    assert gap["unlabeled_waiting"] == 1 and gap["print_attempt"] == "unknown"
    assert data["gaps"]["not_scanned_in"][0]["labeled_waiting"] == 2


def test_label_confirm_can_cover_selected_pieces_only(client, stack_factory):
    stack = stack_factory(count=3)
    chosen = [stack["specimens"][0]["id"]]
    result = client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]], "specimen_ids": chosen})
    assert result.status_code == 200 and result.json()["label_count"] == 1
    data = dash(client)
    assert data["gaps"]["needs_labels"][0]["unlabeled_waiting"] == 2
    assert data["gaps"]["not_scanned_in"][0]["labeled_waiting"] == 1
    other = stack_factory(count=1, project="P-OTHER")
    bad = client.post("/api/labels/confirm", json={"stack_ids": [stack["id"]], "specimen_ids": [other["specimens"][0]["id"]]})
    assert bad.status_code == 422


def test_csv_batch_is_named_after_its_file(client):
    content = b"project_number,material_ref,test_code,preconditioning_code,specimen_count\nP-CSV,M,T,AR,2\n"
    draft = client.post("/api/prep-drafts/csv", files={"file": ("week-46-prep.csv", content, "text/csv")}).json()
    assert draft["source"]["filename"] == "week-46-prep.csv" and draft["source"]["format"] == "csv"
    assert client.post(f'/api/prep-drafts/{draft["id"]}/commit').status_code == 200
    assert client.get(f'/api/batches/{draft["id"]}').json()["label"] == "week-46-prep.csv"
