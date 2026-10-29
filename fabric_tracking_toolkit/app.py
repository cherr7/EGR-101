"""Browser upload interface for the UL fabric-tracking toolkit.

Run locally with:
    uvicorn app:app --host 127.0.0.1 --port 8000

Do not expose this development server to the public internet without adding
organizational authentication, HTTPS, access controls, and retention policies.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse

import prep_reader
import sample_barcode

HERE = Path(__file__).resolve().parent
RUNS = HERE / "web_runs"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
SUPPORTED = {".xlsx", ".xlsm", ".docx"}

app = FastAPI(title="UL Fabric Tracking", docs_url=None, redoc_url=None)
RUNS.mkdir(exist_ok=True)


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def page(content: str, title: str = "UL Fabric Tracking") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
:root {{ color-scheme: light; --ink:#182230; --muted:#5c6878; --line:#d9e0e8; --blue:#174ea6; --pale:#f4f7fb; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#f3f6fa; color:var(--ink); font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:960px; margin:36px auto; padding:0 18px 40px; }}
header {{ background:#102c52; color:white; padding:26px 28px; border-radius:16px; margin-bottom:22px; }}
header h1 {{ margin:0 0 5px; font-size:clamp(1.6rem,4vw,2.1rem); }}
header p {{ margin:0; color:#dbe7f6; }}
.card {{ background:white; border:1px solid var(--line); border-radius:14px; padding:22px; margin:16px 0; box-shadow:0 3px 14px #13294b08; }}
h2 {{ margin:0 0 12px; font-size:1.2rem; }}
label {{ display:block; font-weight:650; margin:14px 0 6px; }}
input[type=file], input[type=text], input[type=number] {{ display:block; width:100%; padding:11px 12px; border:1px solid #b9c4d2; border-radius:9px; background:white; font:inherit; }}
small,.muted {{ color:var(--muted); }}
button,.button {{ display:inline-block; border:0; border-radius:9px; background:var(--blue); color:white; padding:11px 16px; font:inherit; font-weight:700; text-decoration:none; cursor:pointer; margin-top:16px; }}
button:hover,.button:hover {{ background:#103c83; }}
.button.secondary {{ background:#e8eef7; color:#173b6d; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }}
.pill {{ display:inline-block; border-radius:999px; background:#e8eef7; padding:4px 10px; font-size:.85rem; font-weight:650; }}
.pill.warn {{ background:#fff0d4; color:#704600; }}
.pill.ok {{ background:#dff5e7; color:#155b32; }}
.notice {{ background:#fff8e9; border:1px solid #efd59e; padding:12px 14px; border-radius:9px; }}
.error {{ background:#fff0f0; border:1px solid #f1b8b8; padding:12px 14px; border-radius:9px; }}
ul {{ padding-left:22px; }}
table {{ width:100%; border-collapse:collapse; font-size:.94rem; }}
th,td {{ text-align:left; vertical-align:top; padding:9px 8px; border-bottom:1px solid var(--line); }}
th {{ background:var(--pale); }}
.tablewrap {{ overflow-x:auto; }}
code {{ overflow-wrap:anywhere; }}
footer {{ color:var(--muted); font-size:.86rem; padding:8px 2px; }}
</style>
</head>
<body><main>
<header><h1>UL Fabric Tracking</h1><p>Prep sheet upload · structured JSON · unique sample IDs · barcode labels</p></header>
{content}
<footer>Prototype interface. Use approved UL/LabWare sample IDs; do not upload confidential documents to an unapproved or publicly exposed server.</footer>
</main></body></html>""")


def run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id or ""):
        raise HTTPException(status_code=400, detail="Invalid workflow ID.")
    path = RUNS / run_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Workflow not found. Upload the prep sheet again.")
    return path


def download_link(run_id: str, filename: str, label: str, secondary: bool = False) -> str:
    cls = "button secondary" if secondary else "button"
    return f'<a class="{cls}" href="/download/{run_id}/{filename}">{esc(label)}</a>'


@app.get("/", response_class=HTMLResponse)
def home():
    content = """
<section class="card">
<h2>1. Upload a prep sheet</h2>
<p class="muted">Supported formats: Excel <code>.xlsx</code>, macro-enabled Excel <code>.xlsm</code>, and Word <code>.docx</code>. Maximum file size: 25 MB.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
<label for="prep">Prep sheet document *</label><input id="prep" name="file" type="file" accept=".xlsx,.xlsm,.docx" required>
<div class="grid">
<div><label for="project">LabWareDx project ID (if not detected)</label><input id="project" name="project" type="text" placeholder="e.g. 2908458D1"></div>
<div><label for="stack_size">Default stack size (if confirmed)</label><input id="stack_size" name="stack_size" type="number" min="1" max="99" placeholder="Leave blank if unknown"></div>
<div><label for="sheet">Worksheet name (Excel only, optional)</label><input id="sheet" name="sheet" type="text" placeholder="e.g. Prep"></div>
</div>
<button type="submit">Read document and extract JSON</button>
</form>
</section>
<section class="card">
<h2>How it works</h2>
<ol>
<li>Upload the prep sheet; the toolkit extracts candidate tests and conditions.</li>
<li>Review the extracted JSON and warnings before using it.</li>
<li>Download the ID request JSON and send it to the approved person/system that assigns unique sample IDs.</li>
<li>Upload the returned ID JSON to validate IDs and generate the manifest, printable barcode PNGs, and a ZIP package.</li>
</ol>
<div class="notice"><strong>Important:</strong> the toolkit does not invent official LabWare IDs. If the prep sheet omits a project ID, condition, or stack size, the workflow flags it for review rather than guessing.</div>
</section>
"""
    return page(content)


@app.post("/upload", response_class=HTMLResponse)
async def upload_prep(
    file: UploadFile = File(...),
    project: str = Form(""),
    stack_size: str = Form(""),
    sheet: str = Form(""),
):
    filename = Path(file.filename or "upload").name
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(status_code=400, detail="Unsupported file type. Upload .xlsx, .xlsm, or .docx.")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 25 MB upload limit.")
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    parsed_size = None
    if stack_size.strip():
        try:
            parsed_size = int(stack_size)
            if not 1 <= parsed_size <= 99:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=400, detail="Stack size must be a whole number from 1 to 99.")
    run_id = uuid.uuid4().hex
    folder = RUNS / run_id
    folder.mkdir(parents=True)
    safe_name = f"prep_sheet{suffix}"
    source_path = folder / safe_name
    source_path.write_bytes(raw)
    extraction_path = folder / "extraction.json"
    request_path = folder / "id_request.json"
    try:
        result = prep_reader.read_file(
            str(source_path),
            project=project.strip() or None,
            stack_size=parsed_size,
            sheet=sheet.strip() or None,
        )
        prep_reader.write_json(result, str(extraction_path))
        sample_barcode.export_id_request(str(extraction_path), str(request_path))
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return page(f'<section class="card"><div class="error"><strong>Could not process this document.</strong><p>{esc(exc)}</p></div><a class="button" href="/">Try another file</a></section>', "Upload error")

    jobs = result.get("jobs", [])
    ready = sum(1 for j in jobs if j.get("status") == "ready_for_id_handoff")
    warnings = list(result.get("warnings", []))
    for j in jobs:
        warnings.extend(f"{j.get('test','Unknown test')}: {w}" for w in j.get("warnings", []))
    warning_html = "<p>No extraction warnings were reported.</p>" if not warnings else "<ul>" + "".join(f"<li>{esc(w)}</li>" for w in warnings) + "</ul>"
    rows = []
    for job in jobs:
        status = job.get("status", "review_required")
        pill = "ok" if status == "ready_for_id_handoff" else "warn"
        rows.append(
            "<tr>"
            f"<td>{esc(job.get('test'))}</td><td>{esc(job.get('condition') or '—')}</td>"
            f"<td>{esc(job.get('stack_size') or '—')}</td><td><span class='pill {pill}'>{esc(status.replace('_',' '))}</span></td>"
            f"<td>{esc(job.get('source',{}).get('source_sheet') or '—')}</td></tr>"
        )
    table = ("<div class='tablewrap'><table><thead><tr><th>Test</th><th>Condition</th><th>Stack size</th><th>Status</th><th>Source sheet</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>") if rows else "<p>No candidate jobs were detected. Check the source layout or worksheet selection.</p>"
    content = f"""
<section class="card">
<h2>2. Extraction results</h2>
<p><strong>Source:</strong> {esc(filename)}</p>
<div class="grid">
<div><span class="pill">{len(jobs)} candidate job(s)</span></div>
<div><span class="pill ok">{ready} ready for ID handoff</span></div>
<div><span class="pill warn">{len(jobs)-ready} requiring review</span></div>
</div>
<p><strong>Project ID:</strong> {esc(result.get('project_id') or 'Not detected')}</p>
{download_link(run_id, 'extraction.json', 'Download extraction JSON')}
{download_link(run_id, 'id_request.json', 'Download ID request JSON', True)}
<h2 style="margin-top:24px">Extracted jobs</h2>
{table}
<h2 style="margin-top:24px">Warnings and review items</h2>
{warning_html}
</section>
<section class="card">
<h2>3. Upload the returned sample IDs</h2>
<p>The approved ID-assignment person/system must fill the <code>external_sample_id</code> fields in the ID request and return the JSON. The toolkit validates that each ID exists and is globally unique before making labels.</p>
<form action="/finish/{run_id}" method="post" enctype="multipart/form-data">
<label for="ids">Returned ID JSON *</label><input id="ids" name="id_response" type="file" accept=".json,application/json" required>
<button type="submit">Validate IDs and generate barcode labels</button>
</form>
<p class="muted">If the extraction needs corrections, fix the source or the extracted/ID-request JSON with the approved workflow before generating labels.</p>
</section>
<a class="button secondary" href="/">Start another prep sheet</a>
"""
    return page(content, "Extraction results")


@app.get("/download/{run_id}/{filename}")
def download(run_id: str, filename: str):
    folder = run_dir(run_id)
    allowed = {"extraction.json", "id_request.json", "sample_manifest.json", "labels.csv", "barcode_package.zip"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="File not found.")
    target = folder / filename
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File is not available yet.")
    return FileResponse(target, filename=filename)


@app.post("/finish/{run_id}", response_class=HTMLResponse)
async def finish(run_id: str, id_response: UploadFile = File(...)):
    folder = run_dir(run_id)
    name = Path(id_response.filename or "").name
    if Path(name).suffix.lower() != ".json":
        return page('<section class="card"><div class="error">Upload the returned IDs as a .json file.</div><a class="button" href="/">Start again</a></section>', "Invalid file")
    raw = await id_response.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="ID response exceeds the 25 MB upload limit.")
    response_path = folder / "returned_ids.json"
    response_path.write_bytes(raw)
    manifest_path = folder / "sample_manifest.json"
    labels_dir = folder / "labels"
    try:
        # Parse before import so malformed JSON gives a clean message.
        json.loads(raw.decode("utf-8-sig"))
        manifest = sample_barcode.import_returned_ids(str(response_path), str(manifest_path), str(folder / "id_request.json"))
        labels = sample_barcode.generate_labels(str(manifest_path), str(labels_dir))
    except Exception as exc:
        return page(f"""<section class="card"><h2>Could not generate labels</h2><div class="error"><strong>Validation failed:</strong><p>{esc(exc)}</p></div><p>No barcode package was produced. Correct the returned IDs/stack data and upload the corrected JSON.</p><a class="button" href="/">Start a new workflow</a></section>""", "ID validation failed")

    zip_path = folder / "barcode_package.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, "sample_manifest.json")
        archive.write(folder / "extraction.json", "extraction.json")
        archive.write(folder / "id_request.json", "id_request.json")
        for path in sorted(labels_dir.iterdir()):
            if path.is_file():
                archive.write(path, f"labels/{path.name}")
    content = f"""
<section class="card">
<h2>4. Barcode package generated</h2>
<p><span class="pill ok">Success</span> Validated {len(manifest.get('samples', []))} unique sample IDs and generated {len(labels)} individual barcode labels.</p>
<div class="grid">
<div>{download_link(run_id, 'barcode_package.zip', 'Download complete ZIP package')}</div>
<div>{download_link(run_id, 'sample_manifest.json', 'Download sample manifest', True)}</div>
<div>{download_link(run_id, 'labels.csv', 'Download labels CSV', True)}</div>
</div>
<p>The ZIP contains the extraction JSON, ID request, validated manifest, barcode PNGs, and <code>labels.csv</code>.</p>
<div class="notice"><strong>Before printing:</strong> confirm the job/condition/stack details and test that the printed barcodes scan correctly with the intended scanner.</div>
</section>
<a class="button secondary" href="/">Process another prep sheet</a>
"""
    return page(content, "Barcodes ready")


@app.get("/health")
def health():
    return {"status": "ok"}
