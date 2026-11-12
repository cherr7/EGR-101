"""Read-only intake of real PPE preparation workbooks into editable drafts.

Excel macros, external links, and formulas are never executed. Cached values
are used when present; an uncached formula remains an unresolved review field.
The imported workbook remains unchanged. Nothing here creates labels or scans.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import warnings as python_warnings
from datetime import date, datetime
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
import openpyxl
from defusedxml.ElementTree import fromstring as safe_xml
from defusedxml.common import DefusedXmlException
from openpyxl.utils.cell import range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.utils import get_column_letter
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import get_db
from .models import PrepDraft
from .prepsheet import conditioning_count
from .services import draft_dict

router = APIRouter(prefix="/api", tags=["Workbook intake"])
MAX_UPLOAD = 12_000_000
MAX_EXPANDED = 80_000_000
MAX_ROWS = 1000


def _text(value, number_format: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and re.fullmatch(r"0+", number_format):
        return str(value).zfill(len(number_format))
    return str(value).strip()


def _key(value) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


class SheetReader:
    def __init__(self, sheet, formulas):
        self.sheet, self.formulas = sheet, formulas
        self.origins = {}
        for merged in sheet.merged_cells.ranges:
            for row in range(merged.min_row, merged.max_row + 1):
                for col in range(merged.min_col, merged.max_col + 1):
                    self.origins[(row, col)] = (merged.min_row, merged.min_col)

    def cell(self, row, col):
        row, col = self.origins.get((row, col), (row, col))
        return self.sheet.cell(row, col)

    def value(self, row, col):
        cell = self.cell(row, col)
        return cell.value

    def string(self, row, col):
        cell = self.cell(row, col)
        return _text(cell.value, cell.number_format)

    def source(self, row, col):
        cell = self.cell(row, col)
        formula = self.formulas.cell(cell.row, cell.column)
        result = {"cell": cell.coordinate, "value": _text(cell.value, cell.number_format)}
        if formula.data_type == "f":
            result["formula"] = str(formula.value)
            result["cached"] = cell.value is not None
        return result


def _row(sheet, *, test="", tested_count="", conditions="", conditioning="", sample="", sample_type="",
         cells=None, metadata=None, row_warnings=None):
    """One draft row per prep sheet row, columns in the sheet's order."""
    return {"sheet": sheet, "test": test, "tested_count": tested_count, "conditions": conditions,
            "conditioning": conditioning, "sample": sample, "sample_type": sample_type,
            "metadata": {"cells": cells or {}, "warnings": row_warnings or [], **(metadata or {})}}


def _prep_header(reader):
    for row in range(1, min(reader.sheet.max_row, 20) + 1):
        keys = [_key(reader.value(row, col)) for col in range(1, 7)]
        if keys[0] == "test" and "conditioning" in keys[3] and keys[4] in {"ofsamples", "samples", "numberofsamples", "samplenumber"}:
            return row
    return None


def _prep_rows(reader, filename, header):
    # The values above the table (LabWare #, Oracle project, Standard, Regime) are stated once per sheet.
    context_row = header - 1
    name = reader.sheet.title
    sheet_header = {"project_number": reader.string(context_row, 4), "labware_number": reader.string(context_row, 3),
                    "standard": reader.string(context_row, 7), "regime": reader.string(context_row, 8)}
    header_cells = {"labware": reader.source(context_row, 3), "oracle_project": reader.source(context_row, 4),
                    "standard": reader.source(context_row, 7), "regime": reader.source(context_row, 8)}
    sample_number_column = _key(reader.value(header, 5)) == "samplenumber"
    rows = []
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        origin = reader.origins.get((row, 1), (row, 1))
        test = reader.string(row, 1) if origin == (row, 1) else ""   # lower half of a merged Test cell reads blank, as printed
        treatment, raw_count = reader.string(row, 3), reader.value(row, 4)
        count_source = reader.source(row, 4)
        uncached_count = "formula" in count_source and not count_source.get("cached")
        if not treatment or (raw_count is None and not uncached_count):
            continue
        count, _ = conditioning_count(raw_count)
        # Narrative rows ('Notes', schedules, references) are not specimens.
        if count is None and not uncached_count and not re.search(r"\d", _text(raw_count)):
            continue
        row_warnings, metadata = [], {"import_kind": "ppe_prep", "excel_row": row}
        if sample_number_column:
            sample = reader.string(row, 5)
        else:
            sample = ""
            sample_count, _ = conditioning_count(reader.value(row, 5))
            if sample_count:
                metadata["sample_count"] = sample_count
                row_warnings.append(f"The sheet gives a count of {sample_count} sample(s), not their numbers. Enter the sample numbers.")
            else:
                row_warnings.append("Template row: delete it unless this test/treatment is needed.")
        if count is None:
            row_warnings.append("Pieces per sample is ambiguous or an uncached formula; enter the number of physical pieces.")
        cells = {column: reader.source(row, col) for column, col in
                 (("test", 1), ("tested_count", 2), ("conditions", 3), ("conditioning", 4), ("sample", 5), ("sample_type", 6))}
        cells.update(header_cells)
        rows.append(_row(name, test=test, tested_count=_text(reader.value(row, 2)), conditions=treatment,
                         conditioning=_text(raw_count) if not uncached_count else "", sample=sample,
                         sample_type=reader.string(row, 6), cells=cells, metadata=metadata, row_warnings=row_warnings))
    if not (sheet_header["project_number"] or sheet_header["labware_number"]):
        for item in rows[:1]:
            item["metadata"]["warnings"].append("The sheet has no Oracle project or LabWare #. Enter one above the table.")
    return {name: sheet_header}, rows


def _board_header(reader):
    for row in range(1, min(reader.sheet.max_row, 10) + 1):
        if (_key(reader.value(row, 3)) in {"labware", "labwaredx"}
                and _key(reader.value(row, 4)) in {"project", "oracle"}
                and _key(reader.value(row, 9)) == "test"):
            return row
    return None


def _grouped(name, project, headers, labware=""):
    sheet = name + " · " + (project or labware or "no project")
    headers.setdefault(sheet, {"project_number": project, "labware_number": labware, "standard": "", "regime": ""})
    return sheet


def _board_rows(reader, filename, header):
    headers, rows = {}, []
    invalid = {"", "na", "n/a", "none", "-", "0"}
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        labware, project, test = (reader.string(row, col) for col in (3, 4, 9))
        if not test or not (labware or project):
            continue
        ar, laundered = reader.string(row, 11), reader.string(row, 13)
        treatments = []
        if ar.lower() not in invalid:
            treatments.append(("AR", 11))
        if laundered.lower() not in invalid:
            treatments.append((laundered, 13))   # exact source value, including compound wash notes
        if not treatments:
            treatments = [("", 13)]
        sheet = _grouped(reader.sheet.title, project, headers, labware)
        headers[sheet].update(standard=reader.string(row, 7), regime=reader.string(row, 8))
        for treatment, col in treatments:
            cells = {"labware": reader.source(row, 3), "oracle_project": reader.source(row, 4), "test": reader.source(row, 9),
                     "tested_count": reader.source(row, 10), "conditions": reader.source(row, col)}
            rows.append(_row(sheet, test=test, tested_count=reader.string(row, 10), conditions=treatment, cells=cells,
                             metadata={"import_kind": "ppe_board", "excel_row": row, "source_notes": reader.string(row, 17),
                                       "source_box": reader.string(row, 15)},
                             row_warnings=["A prep board quantity can cover several materials, treatments or tests. Enter pieces per sample.",
                                           "Enter the sample number.",
                                           "Board timestamps are notes only; they are not imported as movements."]))
    return headers, rows


ALIASES = {
    "project_number": {"projectnumber", "project", "oracleproject", "oracle"},
    "material_ref": {"materialref", "material", "sampleid", "materialid", "sampleidentifier", "samplenumber"},
    "test_code": {"testcode", "test", "testname"},
    "preconditioning_code": {"preconditioningcode", "treatment", "condition", "conditions", "laundering"},
    "specimen_count": {"specimencount", "conditioningcount", "piecesforconditioning", "quantity"},
}


def _table_header(reader):
    for row in range(1, min(reader.sheet.max_row, 20) + 1):
        mapping = {}
        for col in range(1, min(reader.sheet.max_column, 50) + 1):
            value = _key(reader.value(row, col))
            for name, aliases in ALIASES.items():
                if value in aliases and name not in mapping:
                    mapping[name] = col
        if all(name in mapping for name in ALIASES):
            return row, mapping
    return None


def _table_rows(reader, filename, header, mapping):
    headers, rows = {}, []
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        values = {name: reader.string(row, col) for name, col in mapping.items()}
        if not any(values.values()):
            continue
        sheet = _grouped(reader.sheet.title, values["project_number"], headers)
        cells = {name: reader.source(row, col) for name, col in mapping.items()}
        rows.append(_row(sheet, test=values["test_code"], conditions=values["preconditioning_code"],
                         conditioning=values["specimen_count"], sample=values["material_ref"], cells=cells,
                         metadata={"import_kind": "table", "excel_row": row}))
    return headers, rows


def inspect_workbook(content: bytes, filename: str, sheet_names: list[str] | None = None) -> dict:
    filename = Path(filename.replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
        raise HTTPException(422, "Upload an Excel .xlsx or .xlsm workbook. Legacy .xls files must first be saved as .xlsx.")
    if not content or len(content) > MAX_UPLOAD:
        raise HTTPException(413, "Workbook must be nonempty and smaller than 12 MB.")
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            files = archive.infolist()
            if (len(files) > 10000 or sum(item.file_size for item in files) > MAX_EXPANDED
                    or any(item.file_size > 5_000_000 and item.file_size > max(item.compress_size, 1) * 500 for item in files)):
                raise HTTPException(413, "Workbook contents are too large to import safely.")
            # openpyxl expands merged cells when loading. Bound their area before
            # loading so a tiny archive cannot request billions of blank cells.
            for item in files:
                if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                    if item.file_size > 12_000_000:
                        raise HTTPException(413, "A worksheet is too large to import safely.")
                    root = safe_xml(archive.read(item))
                    merged_area = 0
                    for node in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell"):
                        left, top, right, bottom = range_boundaries(node.attrib["ref"])
                        merged_area += (right - left + 1) * (bottom - top + 1)
                        if merged_area > 100000:
                            raise HTTPException(413, "Worksheet merged ranges are too large to import safely.")
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("ignore", UserWarning)
            values = openpyxl.load_workbook(io.BytesIO(content), data_only=True, keep_links=False, keep_vba=False)
            formulas = openpyxl.load_workbook(io.BytesIO(content), data_only=False, keep_links=False, keep_vba=False)
    except HTTPException:
        raise
    except (BadZipFile, KeyError, ValueError, OSError, TypeError, ParseError,
            DefusedXmlException, InvalidFileException) as failure:
        raise HTTPException(422, "The workbook could not be read. Check that it opens in Excel and is not encrypted.") from failure
    if sheet_names and any(name not in values.sheetnames for name in sheet_names):
        raise HTTPException(422, "A selected worksheet is not present in this workbook.")
    all_candidates, all_headers, sheets = {}, {}, []
    for sheet in values:
        if _key(sheet.title) in {"formulamap", "namingconventions", "zipperstrengthcalculations"}:
            sheets.append({"name": sheet.title, "kind": "reference", "candidate_count": 0,
                           "warnings": ["Reference or calculation worksheet; no physical specimens imported."]})
            continue
        if sheet.max_row > 20000 or sheet.max_column > 300:
            sheets.append({"name": sheet.title, "kind": "too_large", "candidate_count": 0,
                           "warnings": ["Worksheet dimensions exceed the import limit."]})
            continue
        reader = SheetReader(sheet, formulas[sheet.title])
        prep, board, table = _prep_header(reader), _board_header(reader), _table_header(reader)
        kind, found, candidates = "reference", {}, []
        if prep:
            kind, (found, candidates) = "ppe_prep", _prep_rows(reader, filename, prep)
        elif board:
            kind, (found, candidates) = "ppe_board", _board_rows(reader, filename, board)
        elif table:
            kind, (found, candidates) = "table", _table_rows(reader, filename, *table)
        all_candidates[sheet.title] = candidates
        all_headers[sheet.title] = found
        sheets.append({"name": sheet.title, "kind": kind, "candidate_count": len(candidates),
                       "selected": False, "warnings": [] if candidates else ["No specimen preparation rows detected."]})
    recognized = [item["name"] for item in sheets if item["candidate_count"]]
    selected = sheet_names if sheet_names is not None else recognized[:1]
    rows = [row for name in selected for row in all_candidates.get(name, [])]
    headers = {key: value for name in selected for key, value in all_headers.get(name, {}).items()}
    warnings = ["Check each row against the paper sheet before registering. No scan times were imported."]
    if len(recognized) > 1 and sheet_names is None:
        warnings.append("Only the first preparation worksheet is selected. Choose the needed worksheets before importing other groups.")
    if not rows:
        warnings.append("No importable preparation rows were found. Training matrices and result worksheets are reference documents, not specimen orders.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(422, "Selected worksheets produce more than 1,000 review rows. Import fewer worksheets at a time.")
    for sheet in sheets:
        sheet["selected"] = sheet["name"] in selected
    sha256 = hashlib.sha256(content).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([sha256, selected], sort_keys=True).encode()).hexdigest()
    values.close()
    formulas.close()
    return {"headers": headers, "rows": rows,
            "source": {"filename": filename, "sha256": sha256, "format": Path(filename).suffix.lower().lstrip("."),
                       "selected_sheets": selected, "import_fingerprint": fingerprint,
                       "formulas_executed": False, "macros_executed": False},
            "sheets": sheets, "warnings": warnings}


@router.post("/prep-drafts/workbook")
async def import_workbook(file: UploadFile = File(...), sheet_names: str | None = Form(None),
                          new_batch: bool = Form(False), db: Session = Depends(get_db)):
    selected = None
    if sheet_names is not None:
        try:
            selected = json.loads(sheet_names)
            if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
                raise ValueError()
        except (ValueError, json.JSONDecodeError):
            selected = [name.strip() for name in sheet_names.split(",") if name.strip()]
        if not selected:
            raise HTTPException(422, "Select at least one preparation worksheet.")
    content = await file.read(MAX_UPLOAD + 1)
    parsed = inspect_workbook(content, file.filename or "workbook.xlsx", selected)
    fingerprint = parsed["source"]["import_fingerprint"]
    key = int.from_bytes(bytes.fromhex(fingerprint)[:8], "big", signed=True)
    with db.begin():
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        if not new_batch:
            existing = db.scalar(select(PrepDraft).where(
                PrepDraft.source["import_fingerprint"].astext == fingerprint).order_by(PrepDraft.created_at.desc()))
            if existing:
                return {**draft_dict(existing), "reused_import": True}
        draft = PrepDraft(**parsed)
        db.add(draft)
        db.flush()
        return {**draft_dict(draft), "reused_import": False}
