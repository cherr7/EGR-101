"""Fabric sample ID handoff, per-sample Code 128 labels, and stack scanning.

Barcodes encode the external unique sample ID returned by the ID-assignment step.
A local manifest maps every sample ID to its stack, so scanning any one sample
logs all members of that stack at the same timestamp.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "sample_manifest.json"
DEFAULT_LOG = HERE / "scan_log.csv"

def _key(job: dict) -> tuple:
    return (str(job.get("project_id") or "").strip().upper(),
            re.sub(r"\s+", " ", str(job.get("test") or "")).strip().casefold(),
            re.sub(r"\s+", "", str(job.get("condition") or "")).strip().casefold(),
            str(job.get("stack_id") or "").strip().casefold())

def _safe_id(value: Any) -> str:
    return str(value or "").strip()

def extract(prep_file: str, out_json: str, project: str | None = None,
            stack_size: int | None = None, sheet: str | None = None) -> dict:
    import prep_reader
    result = prep_reader.read_file(prep_file, project=project, stack_size=stack_size, sheet=sheet)
    prep_reader.write_json(result, out_json)
    return result

def export_id_request(extraction_json: str, out_json: str) -> dict:
    """Create an ID request containing only fully reviewed, eligible jobs."""
    source = json.loads(Path(extraction_json).read_text(encoding="utf-8"))
    project_id = _safe_id(source.get("project_id"))

    request = {
        "schema_version": "1.0",
        "purpose": (
            "Assign one unique sample ID to every individual sample; "
            "return IDs in the same job/sample order."
        ),
        "project_id": project_id or None,
        "jobs": [],
        "warnings": list(source.get("warnings", [])),
    }

    for idx, job in enumerate(source.get("jobs", []), start=1):
        test = _safe_id(job.get("test"))
        condition = _safe_id(job.get("condition"))
        job_project = _safe_id(job.get("project_id")) or project_id
        sample_ids = job.get("sample_ids") or []
        count = job.get("stack_size")

        if not count and sample_ids:
            count = len(sample_ids)

        try:
            count = int(count) if count is not None else None
        except (TypeError, ValueError):
            count = None

        reasons = []
        if job.get("status") != "ready_for_id_handoff":
            reasons.append(
                f"extraction status is {job.get('status', 'missing')!r}"
            )
        if not job_project:
            reasons.append("missing project ID")
        if not test:
            reasons.append("missing test name")
        if not condition:
            reasons.append("missing condition")
        if count is None or not 1 <= count <= 99:
            reasons.append("missing or invalid stack size (must be 1–99)")
        if sample_ids and count is not None and len(sample_ids) != count:
            reasons.append("sample ID count does not match stack size")

        if reasons:
            request["warnings"].append(
                f"Job {idx} ({test or 'unnamed test'}) excluded from ID handoff: "
                + "; ".join(reasons)
                + "."
            )
            continue

        request["jobs"].append({
            "job_ref": f"J{idx:03d}",
            "project_id": job_project,
            "test": test,
            "condition": condition,
            "stack_id": job.get("stack_id") or f"STACK-{idx:03d}",
            "stack_size": count,
            "samples": [
                {
                    "sample_no": n,
                    "sample_label": (
                        sample_ids[n - 1]
                        if len(sample_ids) >= n
                        else f"A{n}"
                    ),
                    "external_sample_id": None,
                }
                for n in range(1, count + 1)
            ],
            "status": "awaiting_external_ids",
        })

    if not request["jobs"]:
        request["warnings"].append(
            "No eligible jobs were exported. Review extraction results before ID assignment."
        )

    Path(out_json).write_text(
        json.dumps(request, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return request

def import_returned_ids(
    id_response_json: str,
    out_manifest: str = str(DEFAULT_MANIFEST),
    id_request_json: str | None = None,
) -> dict:
    """Validate returned IDs against the original request and build a manifest."""
    data = json.loads(Path(id_response_json).read_text(encoding="utf-8-sig"))
    errors = []

    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("ID response must be a JSON object containing a jobs list.")

    requested_jobs = None
    if id_request_json:
        request = json.loads(Path(id_request_json).read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict) or not isinstance(request.get("jobs"), list):
            raise ValueError("Original ID request must contain a jobs list.")
        requested_jobs = {}
        for job in request["jobs"]:
            ref = _safe_id(job.get("job_ref"))
            if not ref or ref in requested_jobs:
                raise ValueError(f"Original ID request has a missing or duplicate job_ref: {ref!r}.")
            requested_jobs[ref] = job

    response_jobs = {}
    for job in data["jobs"]:
        if not isinstance(job, dict):
            errors.append("Response contains a malformed job entry.")
            continue
        ref = _safe_id(job.get("job_ref"))
        if not ref:
            errors.append("Response contains a job with no job_ref.")
            continue
        if ref in response_jobs:
            errors.append(f"Duplicate job_ref returned: {ref!r}.")
            continue
        response_jobs[ref] = job

    if requested_jobs is not None:
        missing = sorted(set(requested_jobs) - set(response_jobs))
        extra = sorted(set(response_jobs) - set(requested_jobs))
        if missing:
            errors.append("Missing requested jobs: " + ", ".join(missing) + ".")
        if extra:
            errors.append("Unknown extra jobs: " + ", ".join(extra) + ".")

    entries, seen = [], set()

    for ref, job in response_jobs.items():
        requested = requested_jobs.get(ref) if requested_jobs is not None else None

        if requested is not None:
            for field in ("project_id", "test", "condition", "stack_id"):
                expected = _safe_id(requested.get(field))
                actual = _safe_id(job.get(field))
                if expected != actual:
                    errors.append(
                        f"{ref}: {field} changed from {expected!r} to {actual!r}."
                    )

            try:
                expected_size = int(requested.get("stack_size"))
            except (TypeError, ValueError):
                expected_size = None
            try:
                actual_size = int(job.get("stack_size"))
            except (TypeError, ValueError):
                actual_size = None

            if expected_size != actual_size:
                errors.append(
                    f"{ref}: stack_size is {actual_size!r}; expected {expected_size!r}."
                )

            requested_samples = requested.get("samples")
            if not isinstance(requested_samples, list):
                errors.append(f"{ref}: original request has invalid sample rows.")
                continue
            expected_numbers = [s.get("sample_no") for s in requested_samples]
            returned_samples = job.get("samples")
            if not isinstance(returned_samples, list):
                errors.append(f"{ref}: returned samples must be a list.")
                continue
            returned_numbers = [s.get("sample_no") for s in returned_samples if isinstance(s, dict)]
            if len(returned_samples) != len(requested_samples) or returned_numbers != expected_numbers:
                errors.append(
                    f"{ref}: returned sample rows do not match the original request."
                )
                continue

        size = job.get("stack_size")
        samples = job.get("samples")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= 99:
            errors.append(f"{ref}: invalid stack_size; expected an integer from 1 to 99.")
            continue
        if not isinstance(samples, list) or len(samples) != size:
            errors.append(f"{ref}: sample count does not match stack_size {size}.")
            continue

        sample_numbers = []
        valid_job_samples = []
        for sample in samples:
            if not isinstance(sample, dict):
                errors.append(f"{ref}: malformed sample entry.")
                continue
            number = sample.get("sample_no")
            if not isinstance(number, int) or isinstance(number, bool):
                errors.append(f"{ref}: invalid sample_no {number!r}.")
                continue
            sample_numbers.append(number)
            valid_job_samples.append(sample)

        if sample_numbers != list(range(1, size + 1)):
            errors.append(f"{ref}: sample numbers must be exactly 1 through {size}.")
            continue
        if len(valid_job_samples) != size:
            continue

        for sample in valid_job_samples:
            sample_id = _safe_id(
                sample.get("external_sample_id")
                or sample.get("sample_id")
                or sample.get("id")
            )
            if not sample_id:
                errors.append(f"{ref} sample {sample['sample_no']}: missing returned ID.")
                continue

            folded = sample_id.casefold()
            if folded in seen:
                errors.append(
                    f"Duplicate sample ID returned: {sample_id!r}. IDs must be globally unique."
                )
                continue
            seen.add(folded)

            entries.append({
                "sample_id": sample_id,
                "project_id": job.get("project_id"),
                "test": job.get("test"),
                "condition": job.get("condition"),
                "stack_id": job.get("stack_id") or ref,
                "sample_no": sample["sample_no"],
                "stack_size": size,
                "sample_label": sample.get("sample_label") or f"A{sample['sample_no']}",
            })

    if errors:
        raise ValueError("Cannot import returned IDs:\n- " + "\n- ".join(errors))
    if not entries:
        raise ValueError("No sample IDs found in response.")

    manifest = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "samples": entries,
    }
    Path(out_manifest).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest

def generate_labels(manifest_path: str = str(DEFAULT_MANIFEST), out_dir: str = str(HERE / "labels")) -> list[dict]:
    """Create one PNG label per sample with a Data Matrix and readable metadata."""
    try:
        from reportlab.graphics.barcode.ecc200datamatrix import ECC200DataMatrix
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Label generation requires ReportLab and Pillow. Install them in the project venv."
        ) from exc

    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    samples = data.get("samples", [])
    if not samples:
        raise ValueError("Manifest has no samples.")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    dpi = 300
    width = round(62 / 25.4 * dpi)
    height = round(40 / 25.4 * dpi)
    margin = 24
    font = ImageFont.load_default()
    rows = []

    for sample in samples:
        sample_id = _safe_id(sample.get("sample_id"))
        if not sample_id:
            raise ValueError("Manifest contains a sample with a blank sample_id.")

        code = ECC200DataMatrix(sample_id)
        code.validate()
        code.encode()
        matrix = code._matrix

        # Render crisp square modules with a four-module quiet zone.
        module_px = 6
        quiet_px = 4 * module_px
        symbol_px = len(matrix) * module_px
        dm_size = symbol_px + 2 * quiet_px
        dm = Image.new("RGB", (dm_size, dm_size), "white")
        dm_draw = ImageDraw.Draw(dm)

        for row_index, row in enumerate(matrix):
            for col_index, cell in enumerate(row):
                if cell:
                    x0 = quiet_px + col_index * module_px
                    y0 = quiet_px + row_index * module_px
                    dm_draw.rectangle(
                        (x0, y0, x0 + module_px - 1, y0 + module_px - 1),
                        fill="black",
                    )

        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        project_id = str(sample.get("project_id", ""))
        test = str(sample.get("test", ""))
        condition = str(sample.get("condition", ""))
        sample_no = sample.get("sample_no", "")
        stack_size = sample.get("stack_size", "")
        sample_label = str(sample.get("sample_label", ""))

        # Keep the metadata in a dedicated left column.
        text_x = margin
        text_y = 20
        line_gap = 38
        details = [
            ("PROJECT", project_id),
            ("TEST", test),
            ("CONDITION", condition),
            ("SAMPLE", f"{sample_no} of {stack_size} ({sample_label})"),
            ("SAMPLE ID", sample_id),
        ]

        # Use a bold title if available; otherwise use Pillow's default font.
        try:
            title_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
            )
            detail_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18
            )
        except OSError:
            title_font = font
            detail_font = font

        draw.text((text_x, text_y), "SAMPLE LABEL", fill="black", font=title_font)
        text_y += 42

        # Reserve the right side for the Data Matrix.
        text_right_limit = width - dm_size - 30
        for label, value in details:
            draw.text((text_x, text_y), f"{label}: {value}", fill="black", font=detail_font)
            text_y += line_gap

        # Scale the symbol down only if required to fit the label height.
        if dm_size > height - 16:
            scale = (height - 16) / dm_size
            new_size = max(1, int(dm_size * scale))
            dm = dm.resize((new_size, new_size), Image.Resampling.NEAREST)
            dm_size = new_size

        dm_x = width - dm_size - 16
        dm_y = (height - dm_size) // 2
        img.paste(dm, (dm_x, dm_y))

        filename = re.sub(r"[^A-Za-z0-9_-]+", "_", sample_id)[:60] + ".png"
        label_path = out / filename
        img.save(label_path, dpi=(dpi, dpi))

        row = dict(sample)
        row["barcode_payload"] = sample_id
        row["barcode_type"] = "Data Matrix ECC200"
        row["label_file"] = label_path.name
        rows.append(row)

    csv_path = out / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    return rows

def scan(sample_id: str, direction: str, manifest_path: str = str(DEFAULT_MANIFEST),
         log_path: str = str(DEFAULT_LOG), force: bool = False) -> dict:
    direction = direction.strip().upper()
    if direction not in {"IN", "OUT"}:
        raise ValueError("Direction must be IN or OUT.")
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    samples = manifest.get("samples", [])
    found = next((s for s in samples if _safe_id(s.get("sample_id")).casefold() == sample_id.strip().casefold()), None)
    if not found:
        raise ValueError(f"Unknown sample ID {sample_id!r}; it is not in {manifest_path}.")
    key = _key(found)
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    fields = ["timestamp", "direction", "sample_id", "project_id", "test", "condition", "stack_id", "sample_no", "stack_size"]
    rows = []
    if log.exists():
        with log.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    previous = None
    for row in rows:
        try:
            if _key(row) == key:
                previous = row.get("direction", "").upper()
        except Exception:
            continue
    stack = [s for s in samples if _key(s) == key]
    # Verify stack completeness before recording a physical stack movement.
    expected = int(found["stack_size"])
    actual_nos = {int(s["sample_no"]) for s in stack}
    if len(stack) != expected or actual_nos != set(range(1, expected + 1)):
        raise ValueError(f"Manifest stack is incomplete/ambiguous: expected samples 1..{expected}, found {sorted(actual_nos)}. No scan was logged.")
    if previous == direction and not force:
        return {"logged": False, "reason": f"Stack is already recorded {direction}.", "samples": stack}
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with log.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not rows:
            writer.writeheader()
        for s in sorted(stack, key=lambda x: int(x["sample_no"])):
            writer.writerow({
                "timestamp": timestamp, "direction": direction, "sample_id": s["sample_id"],
                "project_id": s.get("project_id", ""), "test": s.get("test", ""),
                "condition": s.get("condition", ""), "stack_id": s.get("stack_id", ""),
                "sample_no": s["sample_no"], "stack_size": s["stack_size"],
            })
    return {"logged": True, "timestamp": timestamp, "samples": stack, "reason": "Stack movement recorded."}

def main(argv=None):
    parser = argparse.ArgumentParser(description="Fabric prep-sheet extraction, ID handoff, labels, and stack scans.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("extract", help="Read .xlsx/.xlsm/.docx prep sheet into JSON")
    p.add_argument("file"); p.add_argument("--out", default="extraction.json")
    p.add_argument("--project"); p.add_argument("--stack-size", type=int); p.add_argument("--sheet")
    p = sub.add_parser("request-ids", help="Create JSON to send to the ID-assignment person/system")
    p.add_argument("extraction_json"); p.add_argument("--out", default="id_request.json")
    p = sub.add_parser("import-ids", help="Import response JSON containing external_sample_id per sample")
    p.add_argument("response_json"); p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p = sub.add_parser("labels", help="Generate one Code 128 PNG per sample ID")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST)); p.add_argument("--out", default=str(HERE / "labels"))
    p = sub.add_parser("scan", help="Scan one sample ID to log its whole stack IN/OUT")
    p.add_argument("sample_id"); p.add_argument("direction", choices=["IN", "OUT", "in", "out"])
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST)); p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "extract":
            result = extract(args.file, args.out, args.project, args.stack_size, args.sheet)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"\nWrote {args.out}")
        elif args.cmd == "request-ids":
            result = export_id_request(args.extraction_json, args.out)
            print(json.dumps({"jobs": len(result["jobs"]), "warnings": result["warnings"]}, indent=2))
            print(f"Wrote {args.out}")
        elif args.cmd == "import-ids":
            result = import_returned_ids(args.response_json, args.manifest)
            print(f"Imported {len(result['samples'])} unique sample IDs to {args.manifest}")
        elif args.cmd == "labels":
            result = generate_labels(args.manifest, args.out)
            print(f"Generated {len(result)} labels in {args.out}")
        elif args.cmd == "scan":
            result = scan(args.sample_id, args.direction, args.manifest, args.log, args.force)
            print(json.dumps({"logged": result["logged"], "reason": result["reason"], "sample_count": len(result["samples"])}, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

if __name__ == "__main__":
    main()
