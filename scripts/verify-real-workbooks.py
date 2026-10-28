#!/usr/bin/env python3
"""Opt-in, private verification of supplied Excel files through the real API/DB.

This resets only the dedicated, owned fabric_pipeline_test database. Source
files are read-only. Label PDFs stay in memory; no printer jobs are submitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "fabric_pipeline_test"
MARKER = "fabric-conditioning real-workbook verification only"
WORKBOOKS = (
    "FPA 2112 - Dec2025-FilterMethod.xlsm",
    "NFPA 1970 (71) (Garments,Aluminized,MB,Hoods) - May2025.xlsm",
    "Main PPE Prep Board.xlsm",
    "PPE Test Training Matrix - XXX.xlsx",
)
REFERENCES = ("Mail - William Zhang - Outlook.pdf",
              "TEST-Garment-NFPA 1970-QGVO-Datasheet-Final.docx")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def native_environment():
    if os.environ.get("PIPELINE_DATABASE_URL"):
        return {**os.environ, "DATABASE_URL": os.environ["PIPELINE_DATABASE_URL"]}
    spec = importlib.util.spec_from_file_location("pipeline_native_host", ROOT / "deploy/macos/host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.environment(DATABASE)


def source_count(value):
    """Independent source check for the descriptors observed in these files."""
    normalized = " ".join(str(value or "").split()).lower()
    known = {"6 warp, 6 fill": 12, "3 warp, 3 fill": 6,
             "8 warp, 8 fill": 16, "4 contact, 4 spaced": 8,
             "1 24x18": 1, "6 15x15": 6}
    if normalized in known:
        return known[normalized]
    if normalized.isascii() and normalized.isdecimal():
        number = int(normalized)
        return number if 1 <= number <= 500 else None
    return None


def source_identifier(cell):
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and re.fullmatch("0+", cell.number_format):
        return str(value).zfill(len(cell.number_format))
    return str(value).strip()


def successful(response, operation):
    require(response.is_success, operation + " returned HTTP " + str(response.status_code))
    return response.json()


def run(downloads: Path, report_path: Path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    sources = [downloads / name for name in (*WORKBOOKS, *REFERENCES)]
    require(all(path.is_file() for path in sources), "One or more of the six supplied source files are missing.")
    hashes_before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    environment = native_environment()
    url = make_url(environment["DATABASE_URL"])
    require(url.drivername.startswith("postgresql") and url.database == DATABASE,
            "Verification requires the exact dedicated PostgreSQL database fabric_pipeline_test.")
    require(url.host in {"127.0.0.1", "localhost", "::1"}, "Verification is restricted to local PostgreSQL.")
    os.environ.update(environment)
    os.environ["TEST_DATABASE_URL"] = environment["DATABASE_URL"]
    sys.path.insert(0, str(ROOT / "backend"))
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    target = create_engine(url)
    lock = admin.connect()
    try:
        require(lock.scalar(text("SELECT pg_try_advisory_lock(389742158620941)")), "Another pipeline verification is running.")
        exists = lock.execute(text("SELECT oid, shobj_description(oid, 'pg_database') AS note FROM pg_database WHERE datname=:name"), {"name": DATABASE}).mappings().first()
        if exists is None:
            lock.exec_driver_sql('CREATE DATABASE "fabric_pipeline_test"')
        elif exists["note"] != MARKER:
            with target.connect() as connection:
                relations = connection.scalar(text("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_%'"))
                functions = connection.scalar(text("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_%'"))
                extra_schemas = connection.scalar(text("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema','public') AND nspname NOT LIKE 'pg_%'"))
            require(relations == 0 and functions == 0 and extra_schemas == 0,
                    "Refusing to reset an existing unmarked, nonempty database.")
        lock.exec_driver_sql("COMMENT ON DATABASE fabric_pipeline_test IS '" + MARKER + "'")
        with target.begin() as connection:
            connection.exec_driver_sql("DROP SCHEMA public CASCADE")
            connection.exec_driver_sql("CREATE SCHEMA public")
        migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                   cwd=ROOT / "backend", env=os.environ.copy(), capture_output=True, text=True)
        require(migration.returncode == 0, "Migrations failed in the isolated verification database.")

        import openpyxl
        from PIL import Image
        from pypdf import PdfReader
        import zxingcpp
        from fastapi.testclient import TestClient
        from app.barcodes import code_id, label_code
        from app.db import SessionLocal, engine
        from app.main import app
        from app.models import Stack
        from app.workbooks import conditioning_count

        # Regression checks for real panel descriptors and deliberately ambiguous inputs.
        for descriptor, expected in (("1 24x18", 1), ("6 15x15", 6),
                                     ("6 Warp, 6 Fill", 12), ("4 Contact, 4 Spaced", 8)):
            require(conditioning_count(descriptor)[0] == expected, "Conditioning count regression: " + descriptor)
        for descriptor in ("3 or 6", "24 hours", "Notes"):
            require(conditioning_count(descriptor)[0] is None, "Ambiguous count must remain unresolved.")

        summary = {"verified_at": datetime.now(timezone.utc).isoformat(), "status": "running",
                   "database": DATABASE, "active_app_touched": False, "source_files_modified": False,
                   "printer_jobs_submitted": 0, "pdf_files_written": 0,
                   "workbooks": [], "reference_files": list(REFERENCES),
                   "totals": {"worksheets": 0, "candidates": 0, "prep_rows": 0,
                              "board_rows": 0, "reference_worksheets": 0, "blocked_unreviewed_drafts": 0,
                              "source_count_checks": 0, "source_treatment_checks": 0, "source_project_checks": 0,
                              "registered_reviewed_stacks": 0, "registered_specimens": 0,
                              "decoded_label_pages": 0, "redecoded_label_pages": 0,
                              "repeat_imports": 0, "repeat_commits": 0, "new_batch_identity_checks": 0,
                              "manual_test_count_overrides": 0}}
        all_ids = set()
        source_values = {}
        source_formula_workbooks = []
        with TestClient(app) as client:
            def upload(filename, data, sheet, new_batch=False):
                fields = {"sheet_names": json.dumps([sheet])}
                if new_batch:
                    fields["new_batch"] = "true"
                return successful(client.post("/api/prep-drafts/workbook", files={"file": (filename, data)}, data=fields), "Workbook upload")

            def reviewed(rows, tag):
                result = copy.deepcopy(rows)
                for index, row in enumerate(result, 1):
                    # These values authorize no real lab work: this DB is test-only.
                    defaults = {"project_number": "TEST-PIPELINE-000A", "material_ref": f"TEST-{tag}-{index:03d}",
                                "test_code": "TEST-REVIEW", "preconditioning_code": "TEST-REVIEW"}
                    changed = []
                    for field, value in defaults.items():
                        if not row.get(field):
                            row[field] = value
                            changed.append(field)
                    if row.get("specimen_count") is None:
                        row["specimen_count"] = 1
                        changed.append("specimen_count")
                        summary["totals"]["manual_test_count_overrides"] += 1
                    row["metadata"]["pipeline_verification"] = {
                        "test_only": True, "placeholder_fields": changed,
                        "note": "Explicit synthetic review inputs; not an interpretation of missing customer data."}
                return result

            def register(draft, rows):
                successful(client.put(f'/api/prep-drafts/{draft["id"]}', json={"rows": rows}), "Reviewed draft save")
                result = successful(client.post(f'/api/prep-drafts/{draft["id"]}/commit'), "Reviewed registration")
                repeat = successful(client.post(f'/api/prep-drafts/{draft["id"]}/commit'), "Repeated registration")
                require(result["stack_ids"] == repeat["stack_ids"], "Repeated commit changed stack identities.")
                summary["totals"]["repeat_commits"] += 1
                require(len(result["stack_ids"]) == len(rows), "Stack count differs from the reviewed row count.")
                with SessionLocal.begin() as db:
                    for identifier in result["stack_ids"]:
                        db.get(Stack, identifier).is_demo = True
                stacks = [successful(client.get(f"/api/stacks/{identifier}"), "Registered stack lookup")
                          for identifier in result["stack_ids"]]
                for stack, row in zip(stacks, rows):
                    require(stack["specimen_count"] == row["specimen_count"], "Specimen count differs from approved conditioning count.")
                    require(stack["preconditioning_code"] == row["preconditioning_code"], "Treatment changed during registration.")
                    require(stack["metadata"]["source"] == row["metadata"]["source"], "Registration lost source provenance.")
                    identifiers = [item["id"] for item in stack["specimens"]]
                    require(not all_ids.intersection(identifiers), "Different stacks/batches reused a specimen identity.")
                    all_ids.update(identifiers)
                    require([item["number"] for item in stack["specimens"]] == list(range(1, row["specimen_count"] + 1)), "Specimen numbering is incomplete.")
                    require(all(item["ENTRY"] is None and item["EXIT"] is None for item in stack["specimens"]), "Import invented conditioning timestamps.")
                summary["totals"]["registered_reviewed_stacks"] += len(stacks)
                summary["totals"]["registered_specimens"] += sum(len(item["specimens"]) for item in stacks)
                return stacks

            def decode_preview(stacks):
                expected = [item["id"] for stack in stacks for item in stack["specimens"]]
                response = client.post("/api/labels/preview", json={"stack_ids": [item["id"] for item in stacks]})
                require(response.is_success, "Label preview failed.")
                pdf = PdfReader(io.BytesIO(response.content))
                require(len(pdf.pages) == len(expected), "PDF label count differs from registered specimen count.")
                decoded_ids = []
                for page, identifier in zip(pdf.pages, expected):
                    require(abs(float(page.mediabox.width) - 62 / 25.4 * 72) < .01 and abs(float(page.mediabox.height) - 72) < .01,
                            "PDF pages do not use the default 62 x 25.4 mm dimensions.")
                    require("DEMO" in page.extract_text(), "Verification label is not visibly marked DEMO.")
                    images = list(page.images)
                    require(len(images) == 1, "Expected exactly one Data Matrix image per label.")
                    code = zxingcpp.read_barcode(Image.open(io.BytesIO(images[0].data)))
                    require(code is not None and code.format == zxingcpp.BarcodeFormat.DataMatrix, "A printed-label image did not decode as Data Matrix.")
                    require(code.text == label_code(identifier), "Label payload contains anything other than its label code.")
                    decoded_ids.append(code_id(code.text))
                require(decoded_ids == expected, "Decoded PDF identities do not match the registered specimens.")
                return decoded_ids

            for workbook_index, filename in enumerate(WORKBOOKS, 1):
                data = (downloads / filename).read_bytes()
                workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=True, keep_links=False, keep_vba=False)
                formulas = openpyxl.load_workbook(io.BytesIO(data), data_only=False, keep_links=False, keep_vba=False)
                source_formula_workbooks.append(formulas)
                source_values[filename] = workbook
                book_report = {"filename": filename, "worksheets": [], "source_sha256": hashes_before[filename]}
                for sheet_index, sheet in enumerate(workbook, 1):
                    draft = upload(filename, data, sheet.title)
                    rows = draft["rows"]
                    inventory = next(item for item in draft["sheets"] if item["name"] == sheet.title)
                    kind = inventory["kind"]
                    missing = Counter(field for row in rows for field in
                                      ("project_number", "material_ref", "test_code", "preconditioning_code", "specimen_count")
                                      if not row.get(field))
                    sheet_report = {"sheet": sheet.title, "kind": kind, "candidates": len(rows),
                                    "missing_fields": dict(missing), "warnings": len(draft["warnings"]),
                                    "registered_review_examples": 0}
                    book_report["worksheets"].append(sheet_report)
                    summary["totals"]["worksheets"] += 1
                    summary["totals"]["candidates"] += len(rows)
                    duplicate = upload(filename, data, sheet.title)
                    require(duplicate["id"] == draft["id"], "Repeated file/sheet import created a second draft.")
                    summary["totals"]["repeat_imports"] += 1
                    if filename == WORKBOOKS[3]:
                        require(not rows, "Training reference workbook incorrectly created specimen rows.")
                    if kind == "reference":
                        require(not rows, "Reference worksheet incorrectly created specimen rows.")
                        summary["totals"]["reference_worksheets"] += 1
                        continue
                    if not rows:
                        continue
                    for row in rows:
                        source = row["metadata"]["source"]
                        require(source["filename"] == filename and source["sheet"] == sheet.title and source["cells"],
                                "Imported candidate lost filename/sheet/cell provenance.")
                        cells = source["cells"]
                        require(all(value.get("cell") for value in cells.values()), "A mapped field has no source-cell address.")
                        if kind in {"ppe_prep", "ppe_board"}:
                            oracle = source_identifier(sheet[cells["oracle_project"]["cell"]])
                            labware = source_identifier(sheet[cells["labware"]["cell"]])
                            require(row["metadata"]["oracle_project_number"] == oracle
                                    and row["metadata"]["labware_number"] == labware,
                                    "Project/LabWare source identifiers changed during import.")
                            require((row.get("project_number") or "") == (oracle or labware),
                                    "Primary project mapping does not match its source identifiers.")
                            summary["totals"]["source_project_checks"] += 1
                        treatment = cells["treatment"]["value"]
                        if kind == "ppe_prep":
                            require(row["preconditioning_code"] == treatment, "Prep treatment text was rewritten.")
                            summary["totals"]["source_treatment_checks"] += 1
                            raw = sheet[cells["conditioning_count"]["cell"]].value
                            expected_count = source_count(raw)
                            require(row["specimen_count"] == expected_count, "Source conditioning count does not match imported count.")
                            if expected_count is not None:
                                summary["totals"]["source_count_checks"] += 1
                        elif kind == "ppe_board":
                            require(row["specimen_count"] is None, "Aggregate board quantity was treated as a physical stack count.")
                    if missing:
                        blocked = client.post(f'/api/prep-drafts/{draft["id"]}/commit')
                        require(blocked.status_code == 422, "Unreviewed missing/aggregate fields did not block registration.")
                        require(not successful(client.get(f'/api/prep-drafts/{draft["id"]}'), "Blocked draft lookup")["committed"],
                                "Failed registration partially committed its draft.")
                        summary["totals"]["blocked_unreviewed_drafts"] += 1
                    if kind == "ppe_board":
                        summary["totals"]["board_rows"] += len(rows)
                        selected_rows = rows[:1]  # One explicitly synthetic physical-stack example per board sheet.
                    elif kind == "ppe_prep":
                        summary["totals"]["prep_rows"] += len(rows)
                        selected_rows = rows  # Every detected test/treatment candidate in every prep worksheet.
                    else:
                        selected_rows = rows
                    reviewed_rows = reviewed(selected_rows, f"W{workbook_index}-S{sheet_index}")
                    stacks = register(draft, reviewed_rows)
                    first_ids = decode_preview(stacks)
                    summary["totals"]["decoded_label_pages"] += len(first_ids)
                    second_ids = decode_preview(stacks)
                    summary["totals"]["redecoded_label_pages"] += len(second_ids)
                    require(first_ids == second_ids, "Reprinting changed specimen IDs.")
                    sheet_report["registered_review_examples"] = len(stacks)
                    recovered = upload(filename, data, sheet.title)
                    require(recovered["committed"] and recovered["stack_ids"] == [item["id"] for item in stacks],
                            "Repeated import did not recover the existing registration.")
                    summary["totals"]["repeat_imports"] += 1
                    # Verify transport IDs against the real API and stack linkage.
                    for stack in stacks:
                        for specimen in (stack["specimens"][0], stack["specimens"][-1]):
                            lookup = successful(client.post("/api/lookup", json={"code": label_code(specimen["id"])}), "Decoded-label lookup")
                            require(lookup["id"] == stack["id"] and lookup["scanned_specimen_id"] == specimen["id"],
                                    "Decoded specimen did not resolve to the registered stack.")
                    if kind == "ppe_prep":
                        # A deliberate new physical batch must not reuse the first batch's IDs.
                        fresh = upload(filename, data, sheet.title, new_batch=True)
                        require(fresh["id"] != draft["id"], "Explicit new batch reused the existing draft.")
                        # Identical prep values, including the same material, must
                        # still yield a distinct set of physical-batch identities.
                        fresh_stacks = register(fresh, copy.deepcopy(reviewed_rows[:1]))
                        require(all(fresh_stacks[0][field] == stacks[0][field] for field in
                                    ("project_number", "material_ref", "test_code", "preconditioning_code", "specimen_count")),
                                "New-batch verification did not retain identical prep values.")
                        fresh_ids = decode_preview(fresh_stacks)
                        summary["totals"]["decoded_label_pages"] += len(fresh_ids)
                        require(decode_preview(fresh_stacks) == fresh_ids, "New batch reprint changed specimen identities.")
                        summary["totals"]["redecoded_label_pages"] += len(fresh_ids)
                        summary["totals"]["new_batch_identity_checks"] += 1
                summary["workbooks"].append(book_report)
                print(filename + ": " + str(len(book_report["worksheets"])) + " worksheets verified")
            # PDF/email and Word result templates remain reference inputs, not Excel orders.
            for filename in REFERENCES:
                response = client.post("/api/prep-drafts/workbook", files={"file": (filename, (downloads / filename).read_bytes())})
                require(response.status_code == 422, "Non-Excel background document was accepted as a specimen order.")
            with SessionLocal() as db:
                require(db.scalar(text("SELECT count(*) FROM print_jobs")) == 0, "Verification submitted a printer job.")
                require(db.scalar(text("SELECT count(*) FROM conditioning_sessions")) == 0, "Import created conditioning movements.")
                require(db.scalar(text("SELECT count(*) FROM stacks WHERE is_demo=false")) == 0, "Verification registered unmarked production stacks.")
        for workbook in (*source_values.values(), *source_formula_workbooks):
            workbook.close()
        require(hashes_before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
                "A source file changed during verification.")
        summary["status"] = "passed"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2) + "\n")
        report_path.chmod(0o600)
        print(json.dumps(summary["totals"], indent=2))
        print("PASS: isolated database " + DATABASE + "; aggregate-only report: " + str(report_path))
        engine.dispose()
        return summary
    finally:
        target.dispose()
        lock.close()
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly reset and use only fabric_pipeline_test.")
    parser.add_argument("--downloads", type=Path, default=Path.home() / "Downloads")
    parser.add_argument("--report", type=Path, default=ROOT / "output/qa/excel-pipeline-verification.json")
    args = parser.parse_args()
    if not args.run:
        parser.error("Add --run to opt in. This resets only the owned fabric_pipeline_test database.")
    try:
        run(args.downloads.resolve(), args.report.resolve())
    except (AssertionError, ValueError, OSError) as error:
        raise SystemExit("Verification failed: " + str(error))


if __name__ == "__main__":
    main()
