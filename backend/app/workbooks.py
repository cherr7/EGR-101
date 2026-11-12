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


def conditioning_count(value) -> tuple[int | None, list[dict]]:
    """Only interpret unambiguous piece counts, retaining named subgroups."""
    source = _text(value)
    if re.fullmatch(r"\d+(?:\.0+)?", source):
        count = int(float(source))
        return (count if 1 <= count <= 500 else None), []
    # Real prep templates also write a quantity followed by panel dimensions,
    # e.g. '6 15x15'. Dimensions describe each piece; they are not multipliers.
    panel = re.fullmatch(r'(\d+)\s+\d+(?:\.\d+)?\s*[x×]\s*\d+(?:\.\d+)?(?:\s*(?:in|inch|inches|cm|mm|["″]))?', source, re.I)
    if panel:
        count = int(panel.group(1))
        return (count if 1 <= count <= 500 else None), []
    # These are physical subdivisions, not a sequence such as '24 hours'.
    pieces = re.split(r"\s*(?:,|\+|;|\band\b)\s*", source, flags=re.I)
    groups = []
    for piece in pieces:
        match = re.fullmatch(r"(\d+)\s*(Warp|Fill|Contact|Spaced|Length|Width)\s*", piece, re.I)
        if not match:
            return None, []
        groups.append({"count": int(match.group(1)), "label": match.group(2)})
    total = sum(item["count"] for item in groups)
    return (total if 1 <= total <= 500 else None), groups


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


def _candidate(filename, sheet, cells, *, project="", material="", test="", treatment="",
               count=None, metadata=None, row_warnings=None):
    details = {"source": {"filename": filename, "sheet": sheet, "cells": cells},
               "warnings": row_warnings or [], **(metadata or {})}
    return {"project_number": project or None, "material_ref": material or None,
            "test_code": test or None, "preconditioning_code": treatment or None,
            "specimen_count": count, "metadata": details}


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
    """Return (review rows, sheet notes) for one preparation worksheet."""
    context_row = _context_row(reader, header)
    labware = reader.string(context_row, 3)
    oracle = reader.string(context_row, 4)
    standard = reader.string(context_row, 7)
    regime = reader.string(context_row, 8)
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
                             "not a specimen count. No labels were created for it; add a stack by hand if it needs tracking.")
            continue
        source_row = row
        treatment, raw_count = reader.string(row, 3), reader.value(row, 4)
        count_source = reader.source(row, 4)
        uncached_count = "formula" in count_source and not count_source.get("cached")
        sample_origin = reader.origin(row, 5)
        material = reader.string(row, 5) if sample_number_column else ""
        sample_count, _ = conditioning_count(reader.value(row, 5)) if not sample_number_column else (1 if material else None, [])
        active = bool(sample_count and sample_count > 0)
        if treatment or raw_count is not None or uncached_count:
            block = {"test": reader.origin(row, 1), "samples": sample_origin, "row": row}
        elif (active and block and block["test"] == reader.origin(row, 1) and block["samples"] != sample_origin):
            # A second sample type under the same test ('Coats' / 'Pants') has its
            # own sample count but shares the Conditions and count written above it.
            source_row = block["row"]
            treatment, raw_count = reader.string(source_row, 3), reader.value(source_row, 4)
            count_source = reader.source(source_row, 4)
            uncached_count = "formula" in count_source and not count_source.get("cached")
        has_count = raw_count is not None or uncached_count
        count, groups = conditioning_count(raw_count)
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
                        "raw_count": raw_count, "count": count, "groups": groups, "active": active,
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
    rows = []
    for item in records:
        row, source_row, active = item["row"], item["source_row"], item["active"]
        treatment, count, sample_count = item["treatment"], item["count"], item["sample_count"]
        identifiers = names.get(item["carried"] or (item["comment_origin"], item["sample_origin"])) or []
        row_warnings = []
        if not (oracle or labware):
            row_warnings.append("Project identifier is blank in the prep workbook.")
        if not active:
            row_warnings.append("Template row: confirm this test/treatment is required before registering labels.")
        if not treatment:
            row_warnings.append("Conditions is blank in the prep workbook; enter the treatment.")
        if count is None:
            row_warnings.append("Conditioning count is ambiguous or uncached; enter the number of physical pieces.")
        if sample_count and sample_count > 50:
            row_warnings.append("The sample count is unusually large; split into actual material groups during review.")
        shares_samples = bool(SHARED_SAMPLES.search(item["notes"]))
        if shares_samples:
            row_warnings.append("The prep sheet says this test shares samples with another test. "
                                "Remove this row unless separate physical pieces are conditioned.")
        if re.fullmatch(r"\d+(?:\.\d+)?", treatment):
            row_warnings.append(f"Treatment is only the number '{treatment}'; confirm the actual condition.")
        source = {name: reader.source(source_row if col in (3, 4) else row, col) for name, col in
                  (("test", 1), ("tested_count", 2), ("treatment", 3),
                   ("conditioning_count", 4), ("sample_count", 5), ("sample_type", 6),
                   ("comments", comment_column))}
        if item["carried"]:
            source["comments"] = {"value": item["comment"], "cell": "carried from test block"}
        source["labware"] = reader.source(context_row, 3)
        source["oracle_project"] = reader.source(context_row, 4)
        source["standard"] = reader.source(context_row, 7)
        source["project_regime"] = reader.source(context_row, 8)
        source["sample_released"] = reader.source(context_row, 11)
        metadata = {"import_kind": "ppe_prep", "labware_number": labware,
                    "oracle_project_number": oracle, "standard": standard,
                    "project_regime": regime, "sample_released": released, "raw_treatment": treatment,
                    "sample_type": reader.string(row, 6), "raw_tested_count": _text(reader.value(row, 2)),
                    "raw_conditioning_count": _text(item["raw_count"]), "sample_count": sample_count,
                    "comments": item["comment"], "piece_groups": item["groups"], "template_row": not active,
                    "shares_samples": shares_samples, **spares.get(item["test_origin"], {})}
        copies = sample_count if sample_count and sample_count <= 50 else 1
        for sample_index in range(1, copies + 1):
            material = item["material"] or (identifiers[sample_index - 1] if len(identifiers) == copies else "")
            notes_here = list(row_warnings)
            if not material:
                notes_here.insert(0, "Enter the actual material/sample identifier; Sample Type is not a material identifier."
                                  + (f" The Comments cell says “{item['comment']}”, which does not name {copies} "
                                     f"{'sample' if copies == 1 else 'samples'}." if item["comment"] and active else ""))
            rows.append(_candidate(filename, reader.sheet.title, source, project=oracle or labware,
                                   material=material, test=item["test"], treatment=treatment, count=count,
                                   metadata={**metadata, "sample_index": sample_index},
                                   row_warnings=notes_here))
    if unused:
        notes.append(f"{unused} template {'row' if unused == 1 else 'rows'} with no “# of samples” "
                     f"{'was' if unused == 1 else 'were'} left out: those tests are not part of this project.")
    return rows, notes


def _board_header(reader):
    for row in range(1, min(reader.sheet.max_row, 10) + 1):
        if (_key(reader.value(row, 3)) in {"labware", "labwaredx"}
                and _key(reader.value(row, 4)) in {"project", "oracle"}
                and _key(reader.value(row, 9)) == "test"):
            return row
    return None


def _board_rows(reader, filename, header):
    rows = []
    invalid = {"", "na", "n/a", "none", "-", "0"}
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        labware, project, test = (reader.string(row, col) for col in (3, 4, 9))
        if not test or not (labware or project):
            continue
        raw_count = reader.string(row, 10)
        ar, laundered = reader.string(row, 11), reader.string(row, 13)
        treatments = []
        if ar.lower() not in invalid:
            treatments.append(("AR", "K"))
        if laundered.lower() not in invalid:
            # Retain the exact source value, including compound wash notes.
            treatments.append((laundered, "M"))
        if not treatments:
            treatments = [("", "M")]
        row_warnings = ["Prep board quantity can aggregate materials, treatments, or tests. Enter a physical stack count.",
                        "Enter the actual material/sample identifier.",
                        "Existing board timestamps are source notes and are not imported as conditioning movements."]
        for treatment, treatment_col in treatments:
            cells = {"labware": reader.source(row, 3), "oracle_project": reader.source(row, 4),
                     "test": reader.source(row, 9), "aggregate_count": reader.source(row, 10),
                     "treatment": reader.source(row, 11 if treatment_col == "K" else 13)}
            rows.append(_candidate(filename, reader.sheet.title, cells, project=project or labware,
                                   test=test, treatment=treatment,
                                   metadata={"import_kind": "ppe_board", "labware_number": labware,
                                             "oracle_project_number": project,
                                             "standard": reader.string(row, 7),
                                             "project_regime": reader.string(row, 8),
                                             "raw_aggregate_count": raw_count,
                                             "raw_treatment": treatment,
                                             "source_notes": reader.string(row, 17),
                                             "source_box": reader.string(row, 15)},
                                   row_warnings=list(row_warnings)))
    return rows


ALIASES = {
    "project_number": {"projectnumber", "project", "oracleproject", "oracle"},
    "material_ref": {"materialref", "material", "sampleid", "materialid", "sampleidentifier"},
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
    rows = []
    for row in range(header + 1, min(reader.sheet.max_row, 5000) + 1):
        values = {name: reader.string(row, col) for name, col in mapping.items()}
        if not any(values.values()):
            continue
        count, groups = conditioning_count(values["specimen_count"])
        notes = ["Enter a whole number of physical specimens."] if count is None else []
        for name in ("project_number", "material_ref", "test_code", "preconditioning_code"):
            if not values[name]:
                notes.append("Missing " + name.replace("_", " ") + ".")
        cells = {name: reader.source(row, col) for name, col in mapping.items()}
        rows.append(_candidate(filename, reader.sheet.title, cells,
                               project=values["project_number"], material=values["material_ref"],
                               test=values["test_code"], treatment=values["preconditioning_code"],
                               count=count, metadata={"import_kind": "table", "piece_groups": groups},
                               row_warnings=notes))
    return rows


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
    all_candidates, sheets = {}, []
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
        kind, candidates, notes = "reference", [], []
        if prep:
            kind = "ppe_prep"
            candidates, notes = _prep_rows(reader, filename, prep)
        elif board:
            kind, candidates = "ppe_board", _board_rows(reader, filename, board)
        elif table:
            kind, candidates = "table", _table_rows(reader, filename, *table)
        all_candidates[sheet.title] = candidates
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
    warnings = ["Review physical material groups and conditioning counts before registration. No scan times were imported."]
    if len(recognized) > len(selected) and sheet_names is None:
        warnings.append("Only the filled-in preparation worksheet is selected. Choose the needed worksheets before importing other groups."
                        if filled else
                        "Only the first preparation worksheet is selected. Choose the needed worksheets before importing other groups.")
    for sheet in sheets:
        # A sheet holding only an instruction has no rows to select, but its
        # note must still reach the reviewer.
        if sheet["kind"] == "ppe_prep" and (sheet["name"] in selected or not sheet["candidate_count"]):
            warnings.extend(f'{sheet["name"]} · {note}' for note in sheet["warnings"] if not note.startswith("No specimen"))
    if any(row["metadata"].get("template_row") for row in rows):
        warnings.append("This workbook contains template rows. Remove unneeded tests and fill the project/material before registering.")
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
    return {"rows": rows, "source": {"filename": filename, "sha256": sha256,
                                       "format": Path(filename).suffix.lower().lstrip("."),
                                       "selected_sheets": selected, "import_fingerprint": fingerprint},
            "sheets": sheets, "warnings": warnings, "review_data": {"requires_review": True,
                         "grouping": "One material/sample, test, and exact treatment per physical stack.",
                         "formulas_executed": False, "macros_executed": False}}


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
