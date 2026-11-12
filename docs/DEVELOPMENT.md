# Development

The source is `backend/app` (FastAPI, SQLAlchemy, Alembic) and `frontend/src` (React, Vite). The Mac host scripts are in `deploy/macos`, the Pi station in `deploy/scanner-kiosk`.

## Run and build

After [Mac setup](QUICKSTART.md#1-mac-first-time-setup):

```sh
python3 deploy/macos/host.py start      # database + API on 8120
python3 deploy/macos/host.py status
python3 deploy/macos/host.py stop
```

For frontend work, run `npm run dev` in `frontend/` with `API_URL` pointing at a disposable test backend.

Rebuild after changes:

```sh
cd frontend && npm ci && npm run build && cd ..
python3 deploy/macos/build_app.py          # Mac app → output/macOS/
python3 scripts/build-station.py           # Pi package → output/station-ui/
```

## Tests

Backend integration tests use a separate database whose name ends in `_test` and may reset it. They never touch the app database.

```sh
python3 deploy/macos/host.py test
```

Browser tests create records, so point them at a disposable backend/database:

```sh
cd frontend
npx playwright install chromium
API_URL=http://127.0.0.1:8001 npm run test:e2e
```

Packaging and Pi checks (no browser, no services registered):

```sh
python3 -m unittest discover -s deploy/macos -p test_native.py
python3 -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py'
node deploy/scanner-kiosk/check-package.mjs
```

Real-workbook verification (resets only `fabric_pipeline_test`): see [EXCEL_IMPORT.md](EXCEL_IMPORT.md#repeat-the-verification).

## Useful scripts

| Script | Purpose |
| --- | --- |
| `scripts/deploy-pi.sh` | Build and install a Pi station over SSH |
| `scripts/build-station.py` | Build the Pi station package |
| `scripts/verify-real-workbooks.py` | Excel → registration → label decode check against real workbooks |
| `scripts/barcode-test-sheet.py` | Scanner acceptance sheet (`output/qa/barcode-test-sheet.pdf`) |
| `scripts/scanner-setup-codes.py` | Regenerate the Eyoyo setup codes shown in the app |
| `scripts/seed-test-labels.py` | Register the 20 Test 1 stacks and save their labels |
| `scripts/test1-scan-reliability.py`, `test3-audit-simulation.py`, `scale-simulation.py`, `test-run-sheets.py` | Testing Plan scripts; results in [TEST_RESULTS.md](TEST_RESULTS.md) |
| `scripts/capture-ui.mjs` | Fresh screenshots (below) |

## Screenshots

Run an isolated API backed by `fabric_browser_test` on loopback port 8121, build the frontend, then:

```sh
node scripts/capture-ui.mjs
```

It needs the frontend Playwright dependency and Google Chrome, creates marked DEMO records only on that isolated API, refuses other origins, and never prints.

## Generated `output/` folders

`output/` is created locally and is not in Git.

| Folder | Contents |
| --- | --- |
| `macOS/` | The built Mac app |
| `station-ui/` | The built Pi station package |
| `qa/` | Verification reports, test sheets, disposable checkpoint exports |
| `screenshots/` | Captured interface screenshots |

The older Docker Compose setup was removed; use the native Mac host above.
