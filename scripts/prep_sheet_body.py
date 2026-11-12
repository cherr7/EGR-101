"""Turn flat stack rows into the prep-sheet draft body the API takes (one worksheet per project)."""


def sheet_body(rows):
    headers, sheet_rows = {}, []
    for row in rows:
        sheet = "Project " + row["project_number"]
        headers.setdefault(sheet, {"project_number": row["project_number"]})
        sheet_rows.append({"sheet": sheet, "test": row["test_code"], "conditions": row["preconditioning_code"],
                           "conditioning": str(row["specimen_count"]), "sample": row["material_ref"]})
    return {"headers": headers, "rows": sheet_rows}
