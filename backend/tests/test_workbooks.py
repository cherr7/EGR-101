"""Synthetic, non-customer Excel fixtures exercise intake without Excel/macros."""
import io
import zipfile
from html import escape

import pytest
from fastapi import HTTPException

from app.prepsheet import header_project, split_samples
from app.workbooks import conditioning_count, inspect_workbook, sample_identifiers


def stacks(result):
    """The stacks an imported sheet describes, one per sample, as a reviewer reads them off the
    sheet rows: a blank test continues the one above and each sample number is one stack. A row
    whose sample numbers the sheet doesn't give still shows one unnamed stack per counted sample."""
    found, previous = [], {}
    for row in result["rows"]:
        previous[row["sheet"]] = row["test"] or previous.get(row["sheet"], "")
        samples = split_samples(row["sample"]) or [None] * (row["metadata"].get("sample_count") or 1)
        for sample in samples:
            found.append({"project_number": header_project(result["headers"][row["sheet"]]), "material_ref": sample,
                          "test_code": previous[row["sheet"]], "preconditioning_code": row["conditions"],
                          "specimen_count": conditioning_count(row["conditioning"])[0],
                          "sample_type": row["sample_type"], "metadata": row["metadata"]})
    return found


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


def project_cells(**rows):
    """A filled-in project copy of the template: context row 3, header row 4."""
    return {"C3": "LW-0009D1", "D3": "00073-LAB", "G3": "2112", "H3": "NW", "K3": "000111 (1-A)",
            "A4": "Test", "B4": "Total Number of Specimens to be Tested", "C4": "Conditions",
            "D4": "Specimens per sample for Conditioning", "E4": "# of samples", "F4": "Sample Type",
            "K4": "Comments", **rows}


def test_sample_identifiers_only_accepts_a_complete_list_of_names():
    assert sample_identifiers("Sample A", 1) == ["Sample A"]
    assert sample_identifiers("C1-C6", 6) == ["C1", "C2", "C3", "C4", "C5", "C6"]
    assert sample_identifiers("Sample 3-Sample 20", 18)[::17] == ["Sample 3", "Sample 20"]
    assert sample_identifiers("A-V", 22)[::21] == ["A", "V"]
    assert sample_identifiers("Samples A-C & F", 4) == ["Sample A", "Sample B", "Sample C", "Sample F"]
    assert sample_identifiers("C1-C2, P1", 3) == ["C1", "C2", "P1"]
    assert sample_identifiers("A-V", 21) == []  # names must match '# of samples' exactly
    assert sample_identifiers("If samples are attached, take pictures", 1) == []
    assert sample_identifiers("Sample A-", 1) == []
    assert sample_identifiers("", 1) == []


def test_filled_project_sheet_names_samples_and_drops_unused_template_rows():
    cells = project_cells(
        A5="Flame", B5={"formula": "E5*8", "cached": 8}, C5="AR", D5="3 Warp, 3 Fill", E5=(1, 0),
        F5="Insulation", K5="Sample A", C6="100L", D6="3 Warp, 3 Fill",
        A7="Seams", C7="5L", D7=(6, 0), E7=(3, 0), F7="Seams", K7="A-C",
        A9="Heat", B9={"formula": "E9*3", "cached": 0}, C9="AR", D9=(3, 0),  # template row, not ordered
        A11="Label Heat", C11="AR", E11=(1, 0), K11="Sample A")  # ordered, count entered by hand later
    del cells["E4"], cells["F4"]  # one supplied project copy has lost these two headings
    merges = ("A5:A6", "E5:E6", "F5:F6", "K5:K6", "A7:A8", "E7:E8", "K7:K8")
    result = inspect_workbook(workbook_bytes(cells, merges=merges), "project.xlsm")
    rows = stacks(result)
    assert [(r["test_code"], r["preconditioning_code"], r["material_ref"], r["specimen_count"]) for r in rows] == [
        ("Flame", "AR", "Sample A", 6), ("Flame", "100L", "Sample A", 6),
        ("Seams", "5L", "A", 6), ("Seams", "5L", "B", 6), ("Seams", "5L", "C", 6),
        ("Label Heat", "AR", "Sample A", None)]
    assert all(r["project_number"] == "00073-LAB" and not r["metadata"]["template_row"] for r in rows)
    assert rows[0]["metadata"]["sample_released"] == "000111 (1-A)"
    assert rows[2]["metadata"]["cells"]["comments"]["cell"] == "K7"
    # One sheet row per sheet row: Seams is one row naming three samples, not three rows.
    assert [row["sample"] for row in result["rows"]] == ["Sample A", "Sample A", "A, B, C", "Sample A"]
    assert any("1 template row" in warning for warning in result["warnings"])


def test_sample_types_under_one_test_share_conditions_and_split_the_names():
    cells = project_cells(A5="Shower", C5="AR", D5=(1, 0), E5=(2, 0), F5="Coats", K5="C1-C2, P1",
                          E6=(1, 0), F6="Pants", E7=(0, 0), F7="Hoods")
    result = inspect_workbook(workbook_bytes(cells, merges=("A5:A7", "K5:K7")), "shower.xlsm")
    assert [(r["material_ref"], r["sample_type"], r["preconditioning_code"], r["specimen_count"])
            for r in stacks(result)] == [("C1", "Coats", "AR", 1), ("C2", "Coats", "AR", 1), ("P1", "Pants", "AR", 1)]


def test_unmatched_comment_leaves_material_for_review():
    cells = project_cells(A5="Heat", C5="AR", D5=(3, 0), E5=(2, 0), K5="Sample A")
    rows = stacks(inspect_workbook(workbook_bytes(cells), "mismatch.xlsm"))
    assert [r["material_ref"] for r in rows] == [None, None]
    assert "does not name 2 samples" in rows[0]["metadata"]["warnings"][0]


def test_instruction_row_is_reported_and_never_becomes_a_stack():
    cells = project_cells(A5="ARC Prep", B5="Please cut 7 yards of Sample A-F and return to shipping.",
                          A7="Flame", C7="AR", D7="6 Warp, 6 Fill")
    result = inspect_workbook(workbook_bytes(cells, sheet="ASTM 1506", merges=("A5:A6", "B5:K6"),
                                             extra_sheets=("Formula Map 2025",)), "arc.xlsm")
    assert result["rows"] == []
    assert any("ARC Prep" in warning and "instruction" in warning for warning in result["warnings"])
    assert [sheet["kind"] for sheet in result["sheets"]] == ["ppe_prep", "reference"]


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


def test_sample_names_after_an_instruction_and_longer_prefixes():
    text = "If samples are attached to a shell, take pictures.\nSamples DRD, M-O"
    assert sample_identifiers(text, 4) == ["Sample DRD", "Sample M", "Sample N", "Sample O"]
    assert sample_identifiers("Samples OS1-OS2, TB1-TB2", 4) == ["Sample OS1", "Sample OS2", "Sample TB1", "Sample TB2"]
    assert sample_identifiers("Shares samples with Heat Composite. Additional Prep not needed.", 1) == []


def test_filled_job_assigns_sample_ids_drops_unordered_rows_and_reports_spares():
    result = inspect_workbook(workbook_bytes(filled_cells(), merges=("A5:A6", "B5:B6", "E5:E6", "K5:K6")), "job.xlsm")
    rows = stacks(result)
    # Heat has a blank '# of samples' (tested total 0): not ordered in this job.
    assert {row["test_code"] for row in rows} == {"Flame"}
    assert [(row["preconditioning_code"], row["material_ref"]) for row in rows] == [
        ("AR", "Sample A"), ("AR", "Sample B"), ("AR", "Sample C"),
        ("100L", "Sample A"), ("100L", "Sample B"), ("100L", "Sample C")]
    assert all(row["specimen_count"] == 12 for row in rows)
    meta = rows[0]["metadata"]
    assert meta["sample_released"] == "R-100 (1-A)"
    # 12 + 12 pieces cut per sample x 3 samples = 72 conditioned; 60 tested; 12 spares.
    assert (meta["test_conditioned_pieces"], meta["test_tested_total"], meta["implied_spare_pieces"]) == (72, 60, 12)
    assert not any("material/sample identifier" in w for row in rows for w in row["metadata"]["warnings"])
    assert any("left out" in warning for warning in result["warnings"])


def test_note_row_above_header_does_not_become_project():
    shifted = {}
    for key, value in filled_cells().items():
        row = int(key[1:])
        shifted[f"{key[0]}{row + 1 if row >= 4 else row}"] = value
    shifted["A4"] = "Note to Lab: this standard is part of the pilot program."
    shifted["B6"] = {"formula": "E6*20", "cached": 60}
    shifted["B8"] = {"formula": "E8*D8", "cached": 0}
    rows = stacks(inspect_workbook(workbook_bytes(shifted, merges=("A6:A7", "E6:E7")), "noted.xlsm"))
    assert rows and all(row["project_number"] == "00042-LAB" for row in rows)


def test_seam_template_without_samples_heading_and_merged_instruction_row():
    cells = filled_cells(E4="", F4="", A5="Water Pen", B5={"formula": "((D5-1)*E5)", "cached": 6},
                         C5="5L", D5=(4, 0), E5=(2, 0), K5="Sample 3-Sample 4",
                         C6="", D6="", A7="ARC Prep", B7="", C7="Please cut 7 yards of Sample A-F and return to shipping.",
                         D7="Please cut 7 yards of Sample A-F and return to shipping.")
    rows = stacks(inspect_workbook(workbook_bytes(cells, merges=("C7:F7",)), "seams.xlsm"))
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
