# Fabric Tracking Toolkit

This toolkit is a conservative end-to-end starting point for the UL Solutions fabric-conditioning workflow.

## What it does

1. Reads `.xlsx`, `.xlsm`, and `.docx` source documents.
2. Extracts candidate test/condition rows into JSON.
3. Creates a JSON request for the person/system that assigns unique IDs.
4. Imports the returned IDs and validates that every sample ID is present and globally unique.
5. Generates one Code 128 PNG label per individual sample ID plus `labels.csv`.
6. Scanning **any one** sample ID records every member of that physical stack IN or OUT at the same timestamp.

## Important source-data limit

The supplied Word document is a blank conditioning-log/test-record template. It contains candidate test/condition rows, but it does not establish the LabWare project ID or physical stack size. The reader intentionally flags those jobs for review instead of guessing. A row count of specimen fields in a test report is not necessarily the number of physical samples in the conditioning stack.

For the Excel reader to auto-detect data, use clear headers such as `Test`, `Sample Prep` or `Condition`, `Stack Size`/`Sample Count`, and `LabWare Project ID`. If the workbook layout differs, update `prep_reader.py` mapping rules or provide a representative workbook to tune it.

## Install

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Workflow

### 1. Extract from the prep sheet

```bash
python sample_barcode.py extract "prep_sheet.xlsx" --out extraction.json
```

If the LabWare project ID is known but not printed in the document:

```bash
python sample_barcode.py extract "prep_sheet.xlsx" --project 2908458D1 --out extraction.json
```

To select a particular worksheet:

```bash
python sample_barcode.py extract "prep_sheet.xlsm" --sheet "Prep" --out extraction.json
```

### 2. Make the external ID request

```bash
python sample_barcode.py request-ids extraction.json --out id_request.json
```

Send `id_request.json` to the person/system that assigns sample IDs. The returned file must preserve the job and sample structure and fill each sample's `external_sample_id`. Example:

```json
{
  "jobs": [{
    "job_ref": "J001",
    "project_id": "2908458D1",
    "test": "Flame Resistance Test",
    "condition": "AR",
    "stack_id": "STACK-001",
    "stack_size": 3,
    "samples": [
      {"sample_no": 1, "sample_label": "A1", "external_sample_id": "UNIQUE-ID-001"},
      {"sample_no": 2, "sample_label": "A2", "external_sample_id": "UNIQUE-ID-002"},
      {"sample_no": 3, "sample_label": "A3", "external_sample_id": "UNIQUE-ID-003"}
    ]
  }]
}
```

`stack_id` must distinguish separate physical stacks even if project, test, and condition match. Each individual `external_sample_id` must be unique across the entire response.

### 3. Import IDs and create labels

```bash
python sample_barcode.py import-ids returned_ids.json --manifest sample_manifest.json
python sample_barcode.py labels --manifest sample_manifest.json --out labels
```

Print the PNGs in `labels/` using a printer/label stock that matches the label dimensions and scanner requirements. Test scan readability before production.

### 4. Scan a sample

```bash
python sample_barcode.py scan UNIQUE-ID-001 IN
python sample_barcode.py scan UNIQUE-ID-003 OUT
```

Both commands log every sample in the matching stack, each row sharing one timestamp. A repeated same-direction scan is suppressed unless `--force` is provided.

## Tests

```bash
python -m unittest -v
```

## Before production

- Confirm the ID-return format and whether IDs are globally unique.
- Tune the Excel header mapping against a real prep workbook; the only provided source workbook is absent from the current test materials.
- Confirm whether the external ID should be the literal barcode payload. This implementation uses the returned ID directly in Code 128.
- Confirm whether separate stacks can share the same project/test/condition. If so, the returned file must include a unique `stack_id` for each physical stack.
- Validate printer dimensions, barcode quiet zones, scanner behavior, duplicate scans, and access control for scan logs.
- Keep backups of the manifest and scan log. Do not edit a live manifest while scans are being recorded.


## Browser upload interface

The toolkit now includes a FastAPI browser interface. It connects the existing prep-sheet reader, JSON ID handoff, returned-ID validation, and barcode-label generator.

Install dependencies in the toolkit's virtual environment:

```bash
pip install -r requirements.txt
```

Start the local interface from this folder:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000` in a browser.

### Browser workflow

1. Upload an `.xlsx`, `.xlsm`, or `.docx` prep sheet.
2. Optionally provide a known LabWareDx project ID, confirmed default stack size, or Excel worksheet name.
3. Review extracted jobs and warnings; download `extraction.json` and `id_request.json`.
4. Have the approved UL/LabWare ID-assignment person/system complete the `external_sample_id` values in the ID request and return the JSON.
5. Upload the returned JSON. The toolkit validates IDs and creates one PNG barcode per sample.
6. Download `barcode_package.zip`, containing the JSON files, validated sample manifest, PNG labels, and `labels.csv`.

### Important deployment and workflow notes

- The interface intentionally does not fabricate official sample IDs. Barcode generation requires the approved ID-assignment process to return unique IDs.
- Missing conditions, project IDs, or stack sizes may require human review. Do not bypass warnings without checking the source.
- Uploaded documents and generated files are kept under `web_runs/` on the machine running the server. Establish an approved retention/deletion policy before real use.
- This is a local prototype. It binds to `127.0.0.1` by default. Do not expose it to a network or the internet without UL-approved authentication, authorization, HTTPS, audit logging, and data-handling review.
