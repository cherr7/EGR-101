"""Synthetic, non-customer Excel fixtures exercise intake without Excel/macros."""
import io
import zipfile
from html import escape

import pytest
from fastapi import HTTPException

from app.workbooks import conditioning_count, inspect_workbook


def workbook_bytes(cells, *, sheet="Preparation", merges=(), extra_sheets=()):
    """Minimal OOXML input fixture; values are either text or (number, style)."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        names = [sheet, *extra_sheets]
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1)) + '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1)) + '<Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        archive.writestr("xl/styles.xml", '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="000000"/></numFmts><fonts count="1"><font><sz val="11"/><name val="Arial"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164" applyNumberFormat="1"/></cellXfs></styleSheet>')
        for index, _ in enumerate(names, 1):
            grouped = {}
            for coordinate, value in cells.items() if index == 1 else []:
                row = int("".join(c for c in coordinate if c.isdecimal()))
                if isinstance(value, tuple):
                    val, style = value
                    cell = f'<c r="{coordinate}" s="{style}"><v>{val}</v></c>'
                elif isinstance(value, dict):
                    cache = f'<v>{value["cached"]}</v>' if "cached" in value else ""
                    cell = f'<c r="{coordinate}"><f>{escape(value["formula"])}</f>{cache}</c>'
                else:
                    cell = f'<c r="{coordinate}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
                grouped.setdefault(row, []).append(cell)
            xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(f'<row r="{row}">{"".join(values)}</row>' for row, values in sorted(grouped.items())) + '</sheetData>'
            if merges and index == 1:
                xml += '<mergeCells>' + "".join(f'<mergeCell ref="{item}"/>' for item in merges) + '</mergeCells>'
            archive.writestr(f"xl/worksheets/sheet{index}.xml", xml + '</worksheet>')
    return output.getvalue()


def prep_cells():
    return {"C3": "LW-0009", "D3": "00073-LAB", "G3": "1970(71)", "H3": "NW",
            "A4": "Test", "B4": "Total Number of Specimens to be Tested", "C4": "Conditions",
            "D4": "Specimens per sample for Conditioning", "E4": "# of samples", "F4": "Sample Type",
            "A5": "Flame", "B5": {"formula": "E5*10", "cached": 10}, "C5": "AR",
            "D5": "6 Warp, 6 Fill", "E5": (1, 0), "F5": "Outer shell",
            "C6": "5L->CH", "D6": "4 Contact, 4 Spaced", "E6": (1, 0)}


def test_conditioning_count_includes_extras_and_named_subdivisions():
    assert conditioning_count("1 24x18")[0] == 1
    assert conditioning_count("6 15x15")[0] == 6
    assert conditioning_count("6 Warp, 6 Fill")[0] == 12
    assert conditioning_count("4 Contact, 4 Spaced")[0] == 8
    assert conditioning_count("24 hours")[0] is None
    assert conditioning_count("3 or 6")[0] is None
    assert conditioning_count(0)[0] is None


def test_prep_rows_keep_the_sheet_layout_one_row_per_sheet_row():
    result = inspect_workbook(workbook_bytes(prep_cells(), merges=("A5:A6",)), "Example.xlsm")
    assert result["headers"] == {"Preparation": {"project_number": "00073-LAB", "labware_number": "LW-0009",
                                                 "standard": "1970(71)", "regime": "NW"}}
    first, second = result["rows"]
    assert (first["test"], first["tested_count"], first["conditions"], first["conditioning"], first["sample_type"]) == \
        ("Flame", "10", "AR", "6 Warp, 6 Fill", "Outer shell")
    assert second["test"] == ""            # the lower half of the merged Test cell, as printed
    assert second["conditions"] == "5L->CH" and second["conditioning"] == "4 Contact, 4 Spaced"
    assert first["sample"] == ""           # '# of samples' gives a count, never an invented sample number
    assert first["metadata"]["sample_count"] == 1
    assert first["metadata"]["cells"]["conditioning"]["cell"] == "D5"
    assert result["source"]["macros_executed"] is False


def test_fake_thirty_sample_sheet_registers_one_stack_per_row(client):
    from pathlib import Path
    content = (Path(__file__).parents[2] / "samples/FAKE-prep-30-samples.xlsx").read_bytes()
    draft = client.post("/api/prep-drafts/workbook", files={"file": ("FAKE.xlsx", content)}).json()
    assert len(draft["rows"]) == 30 and draft["problems"] == []
    assert list(draft["headers"]) == ["Preparation"] and draft["headers"]["Preparation"]["project_number"] == "00101-FAKE"
    assert [row["test"] for row in draft["rows"][:5]] == ["Flame (ASTM D6413)", "", "", "", "Flame (ASTM D6413)"]
    assert draft["stack_count"] == 30 and draft["specimen_count"] == 16 * 10 + 8 * 3 + 4 * 8 + 2 * 6
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit').json()
    stacks = [client.get(f"/api/stacks/{identifier}").json() for identifier in committed["stack_ids"]]
    assert [s["material_ref"] for s in stacks] == [f"S-{n:03d}" for n in range(1, 31)]
    assert {s["test_code"] for s in stacks[:4]} == {"Flame (ASTM D6413)"}
    assert stacks[16]["specimen_count"] == 3 and stacks[0]["specimen_count"] == 10


def test_one_row_with_several_sample_numbers_makes_one_stack_each(client):
    body = {"headers": {"P": {"labware_number": "LW-1"}},
            "rows": [{"sheet": "P", "test": "Tear", "conditions": "AR", "conditioning": "3", "sample": "A-1, A-2"},
                     {"sheet": "P", "conditions": "5L", "conditioning": "2", "sample": "A-3",
                      "metadata": {"sample_count": 2}}]}
    draft = client.post("/api/prep-drafts", json=body).json()
    assert draft["problems"] == [{"row": 1, "sheet": "P", "column": "sample",
                                  "message": "The sheet lists 2 sample(s); enter 2 sample number(s)."}]
    body["rows"][1]["sample"] = "A-3; A-4"
    draft = client.put(f'/api/prep-drafts/{draft["id"]}', json=body).json()
    ids = client.post(f'/api/prep-drafts/{draft["id"]}/commit').json()["stack_ids"]
    stacks = [client.get(f"/api/stacks/{i}").json() for i in ids]
    assert [(s["project_number"], s["material_ref"], s["test_code"], s["preconditioning_code"], s["specimen_count"]) for s in stacks] == [
        ("LW-1", "A-1", "Tear", "AR", 3), ("LW-1", "A-2", "Tear", "AR", 3), ("LW-1", "A-3", "Tear", "5L", 2), ("LW-1", "A-4", "Tear", "5L", 2)]


def test_tabular_import_preserves_alphanumeric_and_formatted_leading_zero_project():
    cells = {"A1": "project_number", "B1": "material_ref", "C1": "test_code",
             "D1": "preconditioning_code", "E1": "specimen_count",
             "A2": (73, 1), "B2": "MAT-A", "C2": "D5034", "D2": "AR", "E2": (10, 0),
             "A3": "AB-004", "B3": "MAT-B", "C3": "D2261", "D3": "5L", "E3": (4, 0)}
    result = inspect_workbook(workbook_bytes(cells), "table.xlsx")
    assert [header["project_number"] for header in result["headers"].values()] == ["000073", "AB-004"]
    assert [row["conditioning"] for row in result["rows"]] == ["10", "4"]
    assert [row["sample"] for row in result["rows"]] == ["MAT-A", "MAT-B"]


def test_uncached_formula_requires_review_and_does_not_become_zero():
    cells = prep_cells()
    cells["D5"] = {"formula": "6+6"}
    result = inspect_workbook(workbook_bytes(cells), "example.xlsx")
    # A row with an uncached conditioning formula remains in review, not dropped.
    candidate = next((row for row in result["rows"] if row["conditions"] == "AR"), None)
    assert candidate is not None
    assert candidate["conditioning"] == ""
    assert candidate["metadata"]["cells"]["conditioning"]["cached"] is False


def test_board_counts_are_not_treated_as_physical_stack_or_scan_history():
    cells = {"C1": "Labware", "D1": "Project", "G1": "Standard", "H1": "NW/AR", "I1": "Test",
             "J1": "# Spec", "K1": "AR", "M1": "L", "C2": "LW-A", "D2": "00009",
             "I2": "Flame", "J2": (60, 0), "K2": "x", "M2": (5, 0), "L2": "9/1 12:00PM"}
    result = inspect_workbook(workbook_bytes(cells), "board.xlsm")
    assert len(result["rows"]) == 2
    assert all(row["conditioning"] == "" for row in result["rows"])
    assert all("entered_at" not in row for row in result["rows"])
    assert {row["conditions"] for row in result["rows"]} == {"AR", "5"}


def test_reference_and_invalid_workbooks_fail_clearly():
    result = inspect_workbook(workbook_bytes({"A1": "Training matrix", "B1": "Qualified"}), "training.xlsx")
    assert result["rows"] == []
    assert result["sheets"][0]["kind"] == "reference"
    with pytest.raises(HTTPException) as invalid:
        inspect_workbook(b"not a workbook", "bad.xlsx")
    assert invalid.value.status_code == 422
    with pytest.raises(HTTPException):
        inspect_workbook(b"not a workbook", "legacy.xls")
    with pytest.raises(HTTPException):
        inspect_workbook(workbook_bytes(prep_cells()), "valid.xlsx", ["Missing sheet"])


def test_import_draft_roundtrip_and_duplicate_upload_reuses_ids(client):
    content = workbook_bytes({"A1": "project_number", "B1": "material_ref", "C1": "test_code",
                              "D1": "preconditioning_code", "E1": "specimen_count", "A2": "000A",
                              "B2": "MAT-A", "C2": "Flame", "D2": "AR", "E2": (2, 0)})
    response = client.post("/api/prep-drafts/workbook", files={"file": ("example.xlsx", content)})
    assert response.is_success, response.text
    draft = response.json()
    assert draft["source"]["filename"] == "example.xlsx"
    assert draft["rows"][0]["conditioning"] == "2" and draft["specimen_count"] == 2
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit')
    assert committed.is_success, committed.text
    repeat = client.post("/api/prep-drafts/workbook", files={"file": ("example.xlsx", content)})
    assert repeat.is_success, repeat.text
    assert repeat.json()["id"] == draft["id"] and repeat.json()["reused_import"]
    assert repeat.json()["stack_ids"] == committed.json()["stack_ids"]
    new = client.post("/api/prep-drafts/workbook", files={"file": ("example.xlsx", content)}, data={"new_batch": "true"})
    assert new.json()["id"] != draft["id"]
