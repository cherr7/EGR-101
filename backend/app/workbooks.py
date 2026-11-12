"""Read prep workbooks into drafts. Macros and formulas never run; cached values are used."""
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


_SAMPLE_ID = r"(?:(?i:(samples?))\s*)?#?\s*([A-Z]{0,4})(\d{0,4})"
_SAMPLE_RANGE = re.compile(_SAMPLE_ID + r"(?:\s*(?:-|–|—|(?i:to|thru|through))\s*" + _SAMPLE_ID + r")?")


def sample_identifiers(value, expected: int | None) -> list[str]:
    """Sample names written in the Comments column, e.g. 'Sample A', 'C1-C6',
    'Sample 3-Sample 20', 'A-V'. Returned only when the text is nothing but
    identifiers and names exactly the expected number of samples; anything
    else is an ordinary comment and stays a review field."""
    source = " ".join(_text(value).split())
    if not source or not expected:
        return []
    found = _identifier_list(source, expected)
    # An instruction can come first ('If samples are attached ... Samples DRD, M-O'):
    # then the names start at a later 'Sample(s)' word.
    for word in list(re.finditer(r"(?i)\bsamples?\b", source))[1:] if not found else []:
        found = _identifier_list(source[word.start():], expected)
        if found:
            break
    return found


def _identifier_list(source: str, expected: int) -> list[str]:
    found = []
    for piece in re.split(r"\s*(?:,|;|&|\band\b)\s*", source, flags=re.I):
        match = _SAMPLE_RANGE.fullmatch(piece)
        if not match:
            return []
        word, letters, digits, _, end_letters, end_digits = match.groups()
        if not (letters or digits):
            return []
        # 'Samples A-C & F': the word applies to every name that follows it.
        prefix = "Sample " if word or (found and found[-1].startswith("Sample ")) else ""
        if end_letters is None or not (end_letters or end_digits):
            if piece.rstrip().endswith(("-", "–", "—")) or re.search(r"(?i)\b(?:to|thru|through)\s*$", piece):
                return []
            found.append(prefix + letters + digits)
        elif digits and end_digits and end_letters in ("", letters):
            first, last = int(digits), int(end_digits)
            if not first <= last <= first + 200:
                return []
            width = len(digits) if digits.startswith("0") else 0
            found.extend(prefix + letters + str(number).zfill(width) for number in range(first, last + 1))
        elif not digits and not end_digits and len(letters) == 1 and len(end_letters) == 1 and letters <= end_letters:
            found.extend(prefix + chr(code) for code in range(ord(letters), ord(end_letters) + 1))
        else:
            return []
    return found if len(found) == expected and len(set(found)) == expected else []


class SheetReader:
    def __init__(self, sheet, formulas):
        self.sheet, self.formulas = sheet, formulas
        self.origins = {}
        for merged in sheet.merged_cells.ranges:
            for row in range(merged.min_row, merged.max_row + 1):
                for col in range(merged.min_col, merged.max_col + 1):
                    self.origins[(row, col)] = (merged.min_row, merged.min_col)

    def origin(self, row, col):
        return self.origins.get((row, col), (row, col))

    def cell(self, row, col):
        return self.sheet.cell(*self.origin(row, col))

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
        # Some project copies lose the '# of samples' / 'Sample Type' headings;
        # the columns keep their positions, so a blank heading is accepted.
        if (keys[0] == "test" and "conditioning" in keys[3]
                and keys[4] in {"ofsamples", "samples", "numberofsamples", "samplenumber", ""}
                and (keys[4] or keys[2].startswith("condition"))):
            return row
    return None


def _context_row(reader, header):
    """Row holding the LabWare/Oracle/standard values, just below the 'LabWareDx | Oracle'
    labels. A 'Note to Lab' row can sit between it and the Test header."""
    for row in range(1, header):
        if _key(reader.value(row, 3)).startswith("labware") and _key(reader.value(row, 4)) == "oracle":
            return row + 1
    return header - 1


SHARED_SAMPLES = re.compile(r"shares samples|additional prep not needed", re.I)


def _prep_rows(reader, filename, header):
    """Return ({sheet: header values}, one draft row per sheet row, sheet notes) for one prep worksheet.

    The values above the table (LabWare #, Oracle project, Standard, Regime) are stated once per sheet.
    Each kept row is one sheet row; the sample names the Comments cell gives go into its Sample
    Number cell, comma-separated, so registration makes one stack per sample.
    """
    context_row = _context_row(reader, header)
    name = reader.sheet.title
    labware, oracle = reader.string(context_row, 3), reader.string(context_row, 4)
    sheet_header = {"project_number": oracle, "labware_number": labware,
                    "standard": reader.string(context_row, 7), "regime": reader.string(context_row, 8)}
    header_cells = {"labware": reader.source(context_row, 3), "oracle_project": reader.source(context_row, 4),
                    "standard": reader.source(context_row, 7), "regime": reader.source(context_row, 8),
                    "sample_released": reader.source(context_row, 11)}
    released = reader.string(context_row, 11)
    filled = bool(oracle or labware)
    sample_number_column = _key(reader.value(header, 5)) == "samplenumber"
    comment_column = next((col for col in range(6, min(reader.sheet.max_column, 20) + 1)
                           if _key(reader.value(header, col)) == "comments"), 11)
    records, notes, unused = [], [], 0
    previous_test, block = "", None
    # A comment written on a test's first row also names the samples of the treatment
    # rows below it whose Test cell is merged into it (e.g. Flame AR, then 100L).
    block_comment = {}
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        test = " ".join(reader.string(row, 1).split())
        if test:
            previous_test = test
        # An instruction typed across the Conditions/count columns ('Please cut
        # 7 yards ...') is not a stack of specimens; surface it, never count it.
        if reader.origin(row, 3)[1] != 3 or reader.origin(row, 4)[1] != 4:
            instruction = " ".join(reader.string(row, 3).split())
            if instruction and reader.origin(row, 3)[0] == row:
                notes.append(f"Row {row} · {test or previous_test or 'Note'}: “{instruction}” is an instruction, "
                             "not a specimen count. No labels were created for it; add a row by hand if it needs tracking.")
            continue
        source_row = row
        treatment, raw_count = reader.string(row, 3), reader.value(row, 4)
        count_source = reader.source(row, 4)
        uncached_count = "formula" in count_source and not count_source.get("cached")
        sample_origin = reader.origin(row, 5)
        material = reader.string(row, 5) if sample_number_column else ""
        sample_count = None if sample_number_column else conditioning_count(reader.value(row, 5))[0]
        active = bool(material) if sample_number_column else bool(sample_count)
        if treatment or raw_count is not None or uncached_count:
            block = {"test": reader.origin(row, 1), "samples": sample_origin, "row": row}
        elif active and block and block["test"] == reader.origin(row, 1) and block["samples"] != sample_origin:
            # A second sample type under the same test ('Coats' / 'Pants') has its
            # own sample count but shares the Conditions and count written above it.
            source_row = block["row"]
            treatment, raw_count = reader.string(source_row, 3), reader.value(source_row, 4)
            count_source = reader.source(source_row, 4)
            uncached_count = "formula" in count_source and not count_source.get("cached")
        has_count = raw_count is not None or uncached_count
        count, _ = conditioning_count(raw_count)
        if not active:
            if not treatment or not has_count:
                continue
            # Narrative rows ('Notes', schedules, references) are not specimens.
            if count is None and not uncached_count and (not re.search(r"\d", _text(raw_count))
                                                         or len(_text(raw_count).split()) >= 6):
                continue
            if filled:
                # A filled-in project sheet still lists every test of the
                # template; the ones without a sample count are not being run.
                unused += 1
                continue
        elif not (treatment or has_count):
            continue
        test = test or previous_test
        if not test:
            continue
        test_origin = reader.origin(row, 1)
        comment_origin, comment, carried = reader.origin(row, comment_column), reader.string(row, comment_column), None
        if comment:
            block_comment[test_origin] = (comment_origin, sample_origin)
        elif test_origin in block_comment:
            carried = block_comment[test_origin]
            comment = reader.string(*carried[0])
        records.append({"row": row, "source_row": source_row, "test": test, "treatment": treatment,
                        "raw_count": raw_count, "uncached": uncached_count, "count": count, "active": active,
                        "material": material, "sample_count": sample_count, "sample_origin": sample_origin,
                        "comment_origin": comment_origin, "comment": comment, "carried": carried,
                        "test_origin": test_origin, "tested": reader.value(row, 2),
                        "tested_is_formula": "formula" in reader.source(row, 2),
                        "notes": " ".join(reader.string(row, col) for col in range(7, comment_column + 1))})
    # The Comments cell names the samples. One merged cell can cover several
    # sample-count cells (Coats + Pants), which then take its names in order.
    names = {}
    for origin in dict.fromkeys(item["comment_origin"] for item in records):
        groups_here = {}
        for item in records:
            if item["comment_origin"] == origin and item["active"] and not item["carried"] and not sample_number_column:
                groups_here.setdefault(item["sample_origin"], item["sample_count"])
        found = sample_identifiers(reader.string(*origin), sum(groups_here.values()))
        position = 0
        for sample_origin, size in groups_here.items():
            names[(origin, sample_origin)] = found[position:position + size]
            position += size
    # Spares are never stated. They are the difference between pieces cut for every
    # treatment row of a test (D x E) and that test's tested total (column B), when B
    # is the template's formula. Panels (e.g. Trap Tear '1 24x18') are cut into
    # several test specimens later, so a tested total above the pieces is not a spare.
    spares = {}
    for test_origin in dict.fromkeys(item["test_origin"] for item in records):
        block_rows = [item for item in records if item["test_origin"] == test_origin]
        first = block_rows[0]
        tested, samples = first["tested"], first["sample_count"]
        if (any(item["count"] is None for item in block_rows) or not first["tested_is_formula"]
                or not samples or not isinstance(tested, (int, float)) or tested <= 0):
            continue
        conditioned = sum(item["count"] for item in block_rows) * int(samples)
        if conditioned >= tested:
            spares[test_origin] = {"test_conditioned_pieces": conditioned, "test_tested_total": int(tested),
                                   "implied_spare_pieces": conditioned - int(tested)}
    rows, shown_test = [], None
    for item in records:
        row, active, treatment = item["row"], item["active"], item["treatment"]
        sample_count = item["sample_count"]
        identifiers = names.get(item["carried"] or (item["comment_origin"], item["sample_origin"])) or []
        # The Test cell reads as printed: blank when the sheet leaves it blank (or it is the lower
        # part of a merged cell) and the row above shows the same test. A row whose test heading was
        # left out above it gets the test written in, so a blank always means "same as above".
        written = reader.sheet.cell(row, 1).value if reader.origin(row, 1) == (row, 1) else None
        printed_test = "" if _text(written) == "" and shown_test == item["test"] else item["test"]
        shown_test = item["test"]
        row_warnings = []
        sample = item["material"]
        metadata = {"import_kind": "ppe_prep", "excel_row": row, "sample_released": released,
                    "comments": item["comment"], "template_row": not active, **spares.get(item["test_origin"], {})}
        if not sample_number_column:
            if sample_count:
                metadata["sample_count"] = sample_count
            if sample_count and len(identifiers) == sample_count:
                sample = ", ".join(identifiers)
            elif sample_count:
                row_warnings.append(f"The sheet gives a count of {sample_count} sample(s), not their numbers. Enter the sample numbers."
                                    + (f" The Comments cell says “{item['comment']}”, which does not name "
                                       f"{sample_count} {'sample' if sample_count == 1 else 'samples'}."
                                       if item["comment"] else ""))
        if not active:
            row_warnings.append("Template row: delete it unless this test/treatment is needed.")
        if item["count"] is None:
            row_warnings.append("Pieces per sample is ambiguous or an uncached formula; enter the number of physical pieces.")
        if sample_count and sample_count > 50:
            row_warnings.append("The sample count is unusually large; check it against the paper sheet.")
        if SHARED_SAMPLES.search(item["notes"]):
            metadata["shares_samples"] = True
            row_warnings.append("The prep sheet says this test shares samples with another test. "
                                "Remove this row unless separate physical pieces are conditioned.")
        if re.fullmatch(r"\d+(?:\.\d+)?", treatment):
            row_warnings.append(f"Treatment is only the number '{treatment}'; confirm the actual condition.")
        cells = {column: reader.source(item["source_row"] if col in (3, 4) else row, col) for column, col in
                 (("test", 1), ("tested_count", 2), ("conditions", 3), ("conditioning", 4), ("sample", 5),
                  ("sample_type", 6), ("comments", comment_column))}
        if item["carried"]:
            cells["comments"] = {"value": item["comment"], "cell": "carried from test block"}
        cells.update(header_cells)
        rows.append(_row(name, test=printed_test, tested_count=_text(item["tested"]), conditions=treatment,
                         conditioning="" if item["uncached"] else _text(item["raw_count"]), sample=sample,
                         sample_type=reader.string(row, 6), cells=cells, metadata=metadata, row_warnings=row_warnings))
    if not filled:
        for item in rows[:1]:
            item["metadata"]["warnings"].append("The sheet has no Oracle project or LabWare #. Enter one above the table.")
    if unused:
        notes.append(f"{unused} template {'row' if unused == 1 else 'rows'} with no “# of samples” "
                     f"{'was' if unused == 1 else 'were'} left out: those tests are not part of this project.")
    return {name: sheet_header}, rows, notes


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
        raise HTTPException(422, "Upload an .xlsx or .xlsm file. Save .xls files as .xlsx first.")
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
        raise HTTPException(422, "Can't read this workbook. Check that it opens in Excel.") from failure
    if sheet_names and any(name not in values.sheetnames for name in sheet_names):
        raise HTTPException(422, "A selected worksheet is not present in this workbook.")
    all_candidates, all_headers, sheets = {}, {}, []
    for sheet in values:
        if _key(sheet.title).startswith(("formulamap", "namingconventions", "zipperstrengthcalculations")):
            sheets.append({"name": sheet.title, "kind": "reference", "candidate_count": 0,
                           "warnings": ["Reference or calculation worksheet; no physical specimens imported."]})
            continue
        if sheet.max_row > 20000 or sheet.max_column > 300:
            sheets.append({"name": sheet.title, "kind": "too_large", "candidate_count": 0,
                           "warnings": ["Worksheet dimensions exceed the import limit."]})
            continue
        reader = SheetReader(sheet, formulas[sheet.title])
        prep, board, table = _prep_header(reader), _board_header(reader), _table_header(reader)
        kind, found, candidates, notes = "reference", {}, [], []
        if prep:
            kind, (found, candidates, notes) = "ppe_prep", _prep_rows(reader, filename, prep)
        elif board:
            kind, (found, candidates) = "ppe_board", _board_rows(reader, filename, board)
        elif table:
            kind, (found, candidates) = "table", _table_rows(reader, filename, *table)
        all_candidates[sheet.title] = candidates
        all_headers[sheet.title] = found
        sheets.append({"name": sheet.title, "kind": kind, "candidate_count": len(candidates),
                       "filled_count": sum(not row["metadata"].get("template_row") for row in candidates),
                       "selected": False,
                       "warnings": notes + ([] if candidates else ["No specimen preparation rows detected."])})
    recognized = [item["name"] for item in sheets if item["candidate_count"]]
    # Worksheets with filled-in sample counts are the project's actual work;
    # blank template worksheets in the same file are offered but not preselected.
    filled = [item["name"] for item in sheets if item.get("filled_count")]
    selected = sheet_names if sheet_names is not None else (filled or recognized[:1])
    rows = [row for name in selected for row in all_candidates.get(name, [])]
    headers = {key: value for name in selected for key, value in all_headers.get(name, {}).items()}
    warnings = []
    if len(recognized) > len(selected) and sheet_names is None:
        warnings.append("Only sheets with sample counts are selected. Tick others under Sheets to add them."
                        if filled else
                        "Only the first prep sheet is selected. Tick others under Sheets to add them.")
    for sheet in sheets:
        # A sheet holding only an instruction has no rows to select, but its
        # note must still reach the reviewer.
        if sheet["kind"] == "ppe_prep" and (sheet["name"] in selected or not sheet["candidate_count"]):
            warnings.extend(f'{sheet["name"]} · {note}' for note in sheet["warnings"] if not note.startswith("No specimen"))
    if any(row["metadata"].get("template_row") for row in rows):
        warnings.append("Some rows come from an unfilled template. Remove the tests you aren't running.")
    if not rows:
        warnings.append("No prep rows found in the selected sheets.")
    if len(rows) > MAX_ROWS:
        raise HTTPException(422, "Over 1,000 rows. Import fewer sheets.")
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
def import_workbook(file: UploadFile = File(...), sheet_names: str | None = Form(None),
                    new_batch: bool = Form(False), db: Session = Depends(get_db)):
    # A plain def runs in the thread pool: parsing a large workbook must not stall scans and heartbeats.
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
    content = file.file.read(MAX_UPLOAD + 1)
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
