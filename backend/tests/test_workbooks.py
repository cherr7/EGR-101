"""Synthetic, non-customer Excel fixtures exercise intake without Excel/macros."""
import io
import zipfile
from html import escape

import pytest
from fastapi import HTTPException

from app.workbooks import conditioning_count, inspect_workbook, sample_ids


def workbook_bytes(cells, *, sheet="Preparation", merges=(), extra_sheets=(), sheet_cells=None):
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
            content = cells if index == 1 else (sheet_cells or {}).get(names[index - 1], {})
            for coordinate, value in content.items():
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


def test_ppe_count_uses_conditioning_column_and_preserves_context():
    result = inspect_workbook(workbook_bytes(prep_cells(), merges=("A5:A6",)), "Example.xlsm")
    assert len(result["rows"]) == 2
    first, second = result["rows"]
    assert first["specimen_count"] == 12  # B5 tested=10 must not drop two extra pieces.
    assert first["material_ref"] is None  # 'Outer shell' is a type, not an invented material ID.
    assert first["project_number"] == "00073-LAB"
    assert first["metadata"]["labware_number"] == "LW-0009"
    assert first["metadata"]["project_regime"] == "NW"
    assert first["preconditioning_code"] == "AR"
    assert second["test_code"] == "Flame"
    assert second["preconditioning_code"] == "5L->CH"
    assert second["specimen_count"] == 8
    assert first["metadata"]["source"]["cells"]["conditioning_count"]["cell"] == "D5"
    assert result["review_data"]["macros_executed"] is False


def test_tabular_import_preserves_alphanumeric_and_formatted_leading_zero_project():
    cells = {"A1": "project_number", "B1": "material_ref", "C1": "test_code",
             "D1": "preconditioning_code", "E1": "specimen_count",
             "A2": (73, 1), "B2": "MAT-A", "C2": "D5034", "D2": "AR", "E2": (10, 0),
             "A3": "AB-004", "B3": "MAT-B", "C3": "D2261", "D3": "5L", "E3": (4, 0)}
    rows = inspect_workbook(workbook_bytes(cells), "table.xlsx")["rows"]
    assert rows[0]["project_number"] == "000073"
    assert rows[1]["project_number"] == "AB-004"
    assert [row["specimen_count"] for row in rows] == [10, 4]


def test_uncached_formula_requires_review_and_does_not_become_zero():
    cells = prep_cells()
    cells["D5"] = {"formula": "6+6"}
    result = inspect_workbook(workbook_bytes(cells), "example.xlsx")
    # A row with an uncached conditioning formula remains in review, not dropped.
    candidate = next((row for row in result["rows"] if row["preconditioning_code"] == "AR"), None)
    assert candidate is not None
    assert candidate["specimen_count"] is None
    assert candidate["metadata"]["source"]["cells"]["conditioning_count"]["cached"] is False


def test_board_counts_are_not_treated_as_physical_stack_or_scan_history():
    cells = {"C1": "Labware", "D1": "Project", "G1": "Standard", "H1": "NW/AR", "I1": "Test",
             "J1": "# Spec", "K1": "AR", "M1": "L", "C2": "LW-A", "D2": "00009",
             "I2": "Flame", "J2": (60, 0), "K2": "x", "M2": (5, 0), "L2": "9/1 12:00PM"}
    result = inspect_workbook(workbook_bytes(cells), "board.xlsm")
    assert len(result["rows"]) == 2
    assert all(row["specimen_count"] is None for row in result["rows"])
    assert all("entered_at" not in row for row in result["rows"])
    assert {row["preconditioning_code"] for row in result["rows"]} == {"AR", "5"}


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
    assert draft["rows"][0]["specimen_count"] == 2
    committed = client.post(f'/api/prep-drafts/{draft["id"]}/commit')
    assert committed.is_success, committed.text
    repeat = client.post("/api/prep-drafts/workbook", files={"file": ("example.xlsx", content)})
    assert repeat.is_success, repeat.text
    assert repeat.json()["id"] == draft["id"] and repeat.json()["reused_import"]
    assert repeat.json()["stack_ids"] == committed.json()["stack_ids"]
    new = client.post("/api/prep-drafts/workbook", files={"file": ("example.xlsx", content)}, data={"new_batch": "true"})
    assert new.json()["id"] != draft["id"]


# Layouts below mirror filled-in job prep sheets (synthetic values only).

def filled_cells(**overrides):
    cells = {"C2": "LabWareDx", "D2": "Oracle", "G2": "Standard", "H2": "NW/AR", "K2": "Sample Released",
             "C3": "LW-0001D1", "D3": "00042-LAB", "G3": "2112", "H3": "NW", "K3": "R-100 (1-A)",
             "A4": "Test", "B4": "Total Number of Specimens to be Tested", "C4": "Conditions",
             "D4": "Specimens per sample for Conditioning", "E4": "# of samples", "F4": "Sample Type",
             "K4": "Comments",
             "A5": "Flame", "B5": {"formula": "E5*20", "cached": 60}, "C5": "AR", "D5": "6 Warp, 6 Fill",
             "E5": (3, 0), "F5": "Shell", "K5": "Sample A-C",
             "C6": "100L", "D6": "6 Warp, 6 Fill",
             "A7": "Heat", "B7": {"formula": "E7*D7", "cached": 0}, "C7": "AR", "D7": (3, 0)}
    cells.update(overrides)
    return cells


def test_sample_ids_expand_ranges_and_ignore_instructions():
    assert sample_ids("Sample A-C") == ["A", "B", "C"]
    assert sample_ids("Samples OS1-OS2, TB1-TB2") == ["OS1", "OS2", "TB1", "TB2"]
    assert sample_ids("Sample 3-Sample 5") == ["Sample 3", "Sample 4", "Sample 5"]
    assert sample_ids("A-V")[-1] == "V" and len(sample_ids("A-V")) == 22
    assert sample_ids("If samples are attached to a shell, take pictures.\nSamples DRD, M-O") == ["DRD", "M", "N", "O"]
    assert sample_ids("If samples are attached to a shell, take pictures as 3x12.") is None
    assert sample_ids("Shares samples with Heat Composite. Additional Prep not needed.") is None


def test_filled_job_assigns_sample_ids_drops_unordered_rows_and_reports_spares():
    result = inspect_workbook(workbook_bytes(filled_cells(), merges=("A5:A6", "B5:B6", "E5:E6", "K5:K6")), "job.xlsm")
    rows = result["rows"]
    # Heat has a blank '# of samples' (tested total 0): not ordered in this job.
    assert {row["test_code"] for row in rows} == {"Flame"}
    assert [(row["preconditioning_code"], row["material_ref"]) for row in rows] == [
        ("AR", "A"), ("AR", "B"), ("AR", "C"), ("100L", "A"), ("100L", "B"), ("100L", "C")]
    assert all(row["specimen_count"] == 12 for row in rows)
    meta = rows[0]["metadata"]
    assert meta["sample_released"] == "R-100 (1-A)"
    # 12 + 12 pieces cut per sample x 3 samples = 72 conditioned; 60 tested; 12 spares.
    assert (meta["test_conditioned_pieces"], meta["test_tested_total"], meta["implied_spare_pieces"]) == (72, 60, 12)
    assert not any("material/sample identifier" in w for row in rows for w in row["metadata"]["warnings"])
    assert "were left out" in result["sheets"][0]["warnings"][0]


def test_note_row_above_header_does_not_become_project():
    shifted = {}
    for key, value in filled_cells().items():
        row = int(key[1:])
        shifted[f"{key[0]}{row + 1 if row >= 4 else row}"] = value
    shifted["A4"] = "Note to Lab: this standard is part of the pilot program."
    shifted["B6"] = {"formula": "E6*20", "cached": 60}
    shifted["B8"] = {"formula": "E8*D8", "cached": 0}
    rows = inspect_workbook(workbook_bytes(shifted, merges=("A6:A7", "E6:E7")), "noted.xlsm")["rows"]
    assert rows and all(row["project_number"] == "00042-LAB" for row in rows)


def test_seam_template_without_samples_heading_and_merged_instruction_row():
    cells = filled_cells(E4="", F4="", A5="Water Pen", B5={"formula": "((D5-1)*E5)", "cached": 6},
                         C5="5L", D5=(4, 0), E5=(2, 0), K5="Sample 3-Sample 4",
                         C6="", D6="", A7="ARC Prep", B7="", C7="Please cut 7 yards of Sample A-F and return to shipping.",
                         D7="Please cut 7 yards of Sample A-F and return to shipping.")
    rows = inspect_workbook(workbook_bytes(cells, merges=("C7:F7",)), "seams.xlsm")["rows"]
    assert [(row["test_code"], row["material_ref"], row["specimen_count"]) for row in rows] == [
        ("Water Pen", "Sample 3", 4), ("Water Pen", "Sample 4", 4)]
    assert rows[0]["metadata"]["implied_spare_pieces"] == 2


def test_ordered_sheet_is_selected_even_when_not_first_and_versioned_formula_map_is_reference():
    template = filled_cells(E5="", K5="", C3="", D3="")
    ordered = filled_cells(A5="Weight - Heats", I5="Shares samples with Heat Composite. Additional Prep not needed.")
    result = inspect_workbook(workbook_bytes(template, sheet="Garments", extra_sheets=("Moisture Barriers", "Formula Map 2025"),
                                             sheet_cells={"Moisture Barriers": ordered, "Formula Map 2025": filled_cells()}),
                              "multi.xlsm")
    assert result["source"]["selected_sheets"] == ["Moisture Barriers"]
    assert {sheet["name"]: sheet["kind"] for sheet in result["sheets"]}["Formula Map 2025"] == "reference"
    assert all(row["metadata"]["shares_samples"] for row in result["rows"])
    assert any("shares samples" in w for w in result["rows"][0]["metadata"]["warnings"])
