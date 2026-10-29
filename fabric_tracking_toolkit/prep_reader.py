"""Extract candidate fabric-testing jobs from Excel and Word prep documents.

Important: extraction is conservative. A blank conditioning log does not tell us
how many physical specimens are in a stack or provide a LabWare project ID.
Such records are returned as review_required rather than guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SUPPORTED = {".xlsx", ".xlsm", ".docx"}
PROJECT_LABELS = (
    "labware", "labware id", "labware project id", "project id",
    "project number", "project no", "sample request", "work order",
)
TEST_HINTS = (
    "test", "flame", "shrinkage", "seam", "thread", "thermal stability",
    "water absorption", "label print", "heat resistance",
)
CONDITION_ALIASES = {
    "AR": "AR",
    "100L": "100L",
    "25L": "25L",
    "5L": "5L",
    "AR->5L": "AR->5L",
    "AR → 5L": "AR->5L",
}
ID_PATTERNS = [
    re.compile(r"\b(?:LabWare(?:\s*Dx)?\s*(?:Project\s*)?(?:ID|#|No\.?)|Project\s*(?:ID|#|No\.?))\s*[:#\-]?\s*([A-Z0-9][A-Z0-9\-]{2,})\b", re.I),
]

def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\u00a0", " ")).strip()

def _project_from_text(text: str) -> str | None:
    for pattern in ID_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip().upper()
    return None

def _condition(value: Any) -> str:
    s = _norm(value).replace("→", "->").replace("–", "-").replace("—", "-")
    compact = s.replace(" ", "").upper()
    for known in CONDITION_ALIASES:
        if compact == known.replace(" ", "").replace("→", "->").upper():
            return CONDITION_ALIASES[known]
    return s


def _looks_like_test(value: str) -> bool:
    """Accept ordinary test names from the prep-sheet table."""
    value = _norm(value)
    if not value or value.casefold() in {"test", "none", "n/a"}:
        return False
    if value.startswith("="):
        return False
    return not value.isnumeric()


def _valid_stack_size(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(float(str(value).strip()))
        return n if 1 <= n <= 99 else None
    except (TypeError, ValueError):
        return None


def _read_excel(path: Path, requested_sheet: str | None) -> dict:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError(
            "Reading Excel requires openpyxl. Install with: pip install openpyxl"
        ) from exc

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets = [requested_sheet] if requested_sheet else wb.sheetnames
    missing = [s for s in sheets if s not in wb.sheetnames]
    if missing:
        wb.close()
        raise ValueError(
            f"Worksheet not found: {missing[0]}. Available: {', '.join(wb.sheetnames)}"
        )

    raw_jobs, warnings = [], []
    project = None
    selected_table_found = False

    try:
        for sheet_name in sheets:
            # These sheets contain reference/formula information, not the live
            # sample table. Never treat them as independent jobs.
            if not requested_sheet and sheet_name.casefold() in {
                "formula map", "naming conventions"
            }:
                continue

            ws = wb[sheet_name]
            rows = [
                [_norm(c) for c in row]
                for row in ws.iter_rows(values_only=True)
            ]

            # The LabWareDx value is stored under its header in this template.
            for i, row in enumerate(rows):
                for col, cell in enumerate(row):
                    if cell.casefold().replace(" ", "") in {
                        "labwaredx", "labware", "labwareid",
                        "labwareprojectid", "projectid"
                    }:
                        if i + 1 < len(rows) and col < len(rows[i + 1]):
                            candidate = _norm(rows[i + 1][col])
                            if candidate:
                                project = candidate.upper()
                                break
                if project:
                    break

            # Find the real test table by its three key column headers.
            header_idx = None
            test_col = cond_col = size_col = None

            for i, row in enumerate(rows):
                labels = [
                    re.sub(r"\s+", " ", x.casefold()).strip()
                    for x in row
                ]
                tc = next(
                    (j for j, x in enumerate(labels)
                     if x in {"test", "test name", "test type"}),
                    None,
                )
                cc = next(
                    (j for j, x in enumerate(labels)
                     if x in {"conditions", "condition", "sample prep",
                              "conditioning", "laundering", "prep condition"}),
                    None,
                )
                sc = next(
                    (j for j, x in enumerate(labels)
                     if x in {"# of samples", "number of samples",
                              "sample count", "sample qty", "quantity", "qty"}),
                    None,
                )

                if tc is not None and cc is not None and sc is not None:
                    header_idx, test_col, cond_col, size_col = i, tc, cc, sc
                    break

            if header_idx is None:
                continue

            selected_table_found = True
            current_job = None

            for row_idx in range(header_idx + 1, len(rows)):
                row = rows[row_idx]

                # Stop if another table header begins.
                lowered = [x.casefold().strip() for x in row]
                if any(x in {"test", "test name"} for x in lowered) and any(
                    "condition" in x for x in lowered
                ):
                    break

                test = row[test_col] if test_col < len(row) else ""
                cond = row[cond_col] if cond_col < len(row) else ""
                count_value = row[size_col] if size_col < len(row) else ""
                size = _valid_stack_size(count_value)

                if test and _looks_like_test(test):
                    # Do not guess a count from specimen formulas or totals.
                    current_job = {
                        "test": test,
                        "condition": _condition(cond),
                        "stack_size": size,
                        "sample_ids": [],
                        "source_sheet": sheet_name,
                        "source_row": row_idx + 1,
                    }
                    raw_jobs.append(current_job)

                elif current_job is not None and cond:
                    # In this template, a blank Test cell can continue the
                    # preceding test with another condition (e.g. 100L).
                    normalized = _condition(cond)
                    existing = [
                        x.strip() for x in current_job["condition"].split(",")
                        if x.strip()
                    ]
                    if normalized and normalized not in existing:
                        existing.append(normalized)
                        current_job["condition"] = ", ".join(existing)

            # The named prep sheet is the one we want; don't parse other
            # unrelated sheets once its test table has been found.
            if requested_sheet and selected_table_found:
                break
            if selected_table_found:
                break

    finally:
        wb.close()

    if not selected_table_found:
        warnings.append(
            "No test table with Test, Conditions, and # of samples headers was found."
        )
    if not project:
        warnings.append(
            "No LabWare project ID was found in the workbook metadata."
        )
    if not raw_jobs:
        warnings.append("No test rows were extracted from the selected worksheet.")

    return {
        "project_id": project,
        "jobs": raw_jobs,
        "warnings": warnings,
        "sheets_read": [requested_sheet] if requested_sheet else sheets,
    }

def _read_docx(path: Path) -> dict:
    try:
        from docx import Document
    except ImportError as exc:
        raise RuntimeError("Reading Word documents requires python-docx. Install with: pip install python-docx") from exc

    doc = Document(path)
    texts = [_norm(p.text) for p in doc.paragraphs if _norm(p.text)]
    table_jobs, warnings = [], []
    project = _project_from_text("\n".join(texts))

    for table_index, table in enumerate(doc.tables):
        rows = [[_norm(cell.text) for cell in row.cells] for row in table.rows]
        joined = "\n".join(" | ".join(row) for row in rows)
        project = project or _project_from_text(joined)
        if not rows:
            continue
        header = [x.casefold() for x in rows[0]]
        # Conditioning log layout used by the supplied template: Test, ID, Sample Prep.
        test_col = next((i for i, x in enumerate(header) if x in {"test", "test name", "test type"}), None)
        cond_col = next((i for i, x in enumerate(header) if x in {"sample prep.", "sample prep", "conditioning", "condition"}), None)
        if test_col is None or cond_col is None:
            continue
        for row_idx, row in enumerate(rows[1:], start=2):
            if max(test_col, cond_col) >= len(row):
                continue
            test = row[test_col]
            cond = row[cond_col]
            if not _looks_like_test(test) or not cond:
                continue
            # The "ID" column in this template records which specimens entered conditioning.
            # It is not a project ID and blank IDs do not reveal the physical stack size.
            id_col = next((i for i, x in enumerate(header) if x == "id"), None)
            id_value = row[id_col] if id_col is not None and id_col < len(row) else ""
            size = None
            sample_ids = []
            if id_value and id_value.casefold() != "all":
                sample_ids = [x.strip() for x in re.split(r"[,;\s]+", id_value) if x.strip()]
                # A1-A9 is a confirmed range only if exact sample IDs are present in the cell.
                m = re.fullmatch(r"([A-Za-z]+)(\d+)\s*-\s*([A-Za-z]+)(\d+)", id_value)
                if m and m.group(1).casefold() == m.group(3).casefold():
                    start, end = int(m.group(2)), int(m.group(4))
                    if end >= start and end - start < 99:
                        sample_ids = [f"{m.group(1)}{n}" for n in range(start, end + 1)]
                        size = len(sample_ids)
            table_jobs.append({
                "test": test, "condition": _condition(cond), "stack_size": size,
                "sample_ids": sample_ids, "source_table": table_index, "source_row": row_idx,
            })
    # Deduplicate repeated test/condition rows while preserving order.
    unique, seen = [], set()
    for job in table_jobs:
        key = (job["test"].casefold(), job["condition"].casefold(), tuple(job["sample_ids"]))
        if key not in seen:
            seen.add(key)
            unique.append(job)
    if not project:
        warnings.append("No LabWare project ID was found. The supplied Word template's conditioning-log 'ID' column is for sample IDs, not the LabWare project ID.")
    if unique and all(j["stack_size"] is None for j in unique):
        warnings.append("The document lists test/condition pairs but does not establish physical stack sizes. Confirm each stack size or provide explicit sample IDs (for example A1-A9).")
    if not unique:
        warnings.append("No conditioning-log test rows were found in this Word document.")
    return {"project_id": project, "jobs": unique, "warnings": warnings}

def read_file(file_path: str, project: str | None = None, stack_size: int | None = None, sheet: str | None = None) -> dict:
    """Return normalized extraction JSON for an .xlsx/.xlsm/.docx file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"Unsupported file type {path.suffix!r}. Supported: {', '.join(sorted(SUPPORTED))}")
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        data = _read_excel(path, sheet)
    else:
        data = _read_docx(path)

    final_project = (project or data.get("project_id") or "").strip().upper() or None
    warnings = list(data.get("warnings", []))
    if project and data.get("project_id") and project.strip().upper() != data["project_id"].strip().upper():
        warnings.append(f"Provided project override {project!r} differs from extracted ID {data['project_id']!r}; override used.")
    jobs = []
    for raw in data.get("jobs", []):
        size = raw.get("stack_size") or _valid_stack_size(stack_size)
        sample_ids = raw.get("sample_ids") or []
        if size is None and sample_ids:
            size = len(sample_ids)
        status = "ready_for_id_handoff"
        job_warnings = []
        if not final_project:
            status = "review_required"
            job_warnings.append("Missing LabWare project ID.")
        if not size:
            status = "review_required"
            job_warnings.append("Missing confirmed stack size/sample IDs.")
        if size and not (1 <= size <= 99):
            status = "review_required"
            job_warnings.append("Stack size must be between 1 and 99 for this barcode workflow.")
        if not raw.get("condition"):
            status = "review_required"
            job_warnings.append("Missing condition.")
        jobs.append({
            "project_id": final_project,
            "test": raw["test"],
            "condition": raw.get("condition", ""),
            "stack_size": size,
            "sample_ids": sample_ids,
            "source": {k: raw[k] for k in ("source_sheet", "source_row", "source_table") if k in raw},
            "status": status,
            "warnings": job_warnings,
        })
    if not jobs:
        warnings.append("No candidate jobs were extracted.")
    return {
        "schema_version": "1.0",
        "source_file": path.name,
        "project_id": final_project,
        "jobs": jobs,
        "warnings": warnings,
        "extraction_status": "ready_for_id_handoff" if jobs and all(j["status"] == "ready_for_id_handoff" for j in jobs) else "partial_review_required",
    }

def write_json(result: dict, output_path: str) -> str:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return str(out)
