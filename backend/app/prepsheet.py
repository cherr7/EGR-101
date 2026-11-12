"""Turn prep sheet rows into the stacks they describe.

A draft keeps the sheet's own layout (see schemas.PrepSheetRow): the header values once per
worksheet, then one row per sheet row. Registration expands it here, once, on the server:

- Test: a blank test continues the test above it, exactly as the sheet's merged/blank cells read.
- Project: the worksheet header's Oracle project, or its LabWare # when that is blank.
- Conditions: the treatment, copied as written.
- Specimens per sample for Conditioning: '6', '5 Warp, 5 Fill', '3 15x15' -> pieces per stack.
- Sample Number: one stack per sample. 'S-001, S-002' on one row makes two stacks.
"""
from __future__ import annotations

import re


def text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def conditioning_count(value) -> tuple[int | None, list[dict]]:
    """Only interpret unambiguous piece counts, retaining named subgroups."""
    source = text_value(value)
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


def split_samples(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]", value or "") if item.strip()]


def header_project(header: dict) -> str:
    return (header.get("project_number") or "").strip() or (header.get("labware_number") or "").strip()


def resolve(headers: dict, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (stacks to register, problems). Each problem names the sheet row and column to fix."""
    stacks, problems = [], []
    previous_test: dict[str, str] = {}
    for index, row in enumerate(rows):
        sheet = row.get("sheet") or "Preparation"
        header = headers.get(sheet) or {}
        test = (row.get("test") or "").strip() or previous_test.get(sheet, "")
        if (row.get("test") or "").strip():
            previous_test[sheet] = test
        project = header_project(header)
        treatment = (row.get("conditions") or "").strip()
        count, groups = conditioning_count(row.get("conditioning"))
        samples = split_samples(row.get("sample", ""))

        def problem(column, message):
            problems.append({"row": index, "sheet": sheet, "column": column, "message": message})
        if not project:
            problem("project_number", "Enter the Oracle project or LabWare # for this sheet.")
        if not test:
            problem("test", "Enter the test.")
        if not treatment:
            problem("conditions", "Enter the conditions (treatment).")
        if count is None:
            problem("conditioning", "Enter the pieces per sample, e.g. 6 or 5 Warp, 5 Fill.")
        if not samples:
            problem("sample", "Enter the sample number.")
        expected = row.get("metadata", {}).get("sample_count")
        if isinstance(expected, int) and samples and len(samples) != expected:
            problem("sample", f"The sheet lists {expected} sample(s); enter {expected} sample number(s).")
        if len(set(samples)) != len(samples):
            problem("sample", "A sample number is repeated in this row.")
        if problems and problems[-1]["row"] == index:
            continue
        for sample in samples:
            stacks.append({
                "project_number": project, "material_ref": sample, "test_code": test,
                "preconditioning_code": treatment, "specimen_count": count,
                "metadata": {**{key: value for key, value in row.get("metadata", {}).items() if key != "warnings"},
                             "sheet": sheet, "sheet_row": index, "labware_number": header.get("labware_number", ""),
                             "oracle_project_number": header.get("project_number", ""),
                             "standard": header.get("standard", ""), "project_regime": header.get("regime", ""),
                             "sample_type": row.get("sample_type", ""), "raw_tested_count": row.get("tested_count", ""),
                             "raw_conditioning_count": row.get("conditioning", ""), "piece_groups": groups}})
    return stacks, problems
