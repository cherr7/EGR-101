# Fabric Conditioning Tracker

The latest frontend design uses individual integer IDs, one ENTRY/EXIT pair, binary keyboard scanning and separate staff pages. See the [dedicated scanner implementation guide](docs/SCANNER_IMPLEMENTATION_GUIDE.md) and [current prototype gallery](output/prototypes/round-three/README.md). Those prototypes use a mock API; the Docker application documented below retains the earlier backend model.

A classroom prototype for the UL conditioning-room workflow in `Project Outline.pdf`. It registers individual specimens, prints their permanent QR identities, records whole-stack or selected-specimen movements, and preserves an audit history of supervisor corrections. Routine movement records identify the station; corrections identify the authenticated supervisor.

## Run locally

Requirements: Docker with Compose. The images use Python 3.12, PostgreSQL 18, Node 24, and nginx.

```sh
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Open **http://localhost:8080**. API documentation is at **http://localhost:8080/docs** or **http://localhost:8000/docs**. The default supervisor login is **supervisor / classroom-demo**. These are explicit classroom credentials; `.env` holds the local database and session settings.

First startup registers the three room configurations and stations. It does **not** create specimen data. To create one clearly marked, 20-specimen DEMO stack:

```sh
./scripts/seed-demo.sh
```

Repeating the seed preserves existing demo identities, sessions, and movements. PostgreSQL data persists in the `conditioning-tracker_postgres_data` volume when containers restart. `docker compose down` stops/removes the containers while retaining that volume. None of the supplied scripts resets application data.

On this development Mac, the existing Docker configuration references an unavailable `osxkeychain` helper. To use public images through Colima without changing that authentication configuration, create a separate configuration for the shell session:

```sh
colima start --cpu 2 --memory 4 --disk 20
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
export DOCKER_CONFIG="$(mktemp -d /tmp/conditioning-docker.XXXXXX)"
printf '%s\n' '{"cliPluginsExtraDirs":["/opt/homebrew/lib/docker/cli-plugins"]}' > "$DOCKER_CONFIG/config.json"
docker compose up --build -d
```

The scripts share `scripts/common.sh` and respect those exported `DOCKER_HOST` and `DOCKER_CONFIG` values. This workaround leaves the existing registry credentials and Docker Desktop data untouched.

## Try the workflow

1. Open **Prepare specimens**. Enter rows manually or import [samples/prep-sheet.csv](samples/prep-sheet.csv), review them, then select **Register specimens**. Each row creates a fixed stack with numbered individual specimens. Project numbers are text: both `000000001234` and `UL-2026-001` are valid.
2. Download label **PDF** or **CSV**. Labels contain readable project/material/test/preconditioning details and a versioned opaque specimen or stack ID. Reprints reuse the registered identities. Default stock is 54 × 25 mm; the minimum supported page is 40 × 25 mm. Print the PDF at actual size, or map the exported CSV fields in the chosen printer software. Printing normally happens on a staff PC.
3. Open **Room station**, select the room station, and choose **IN**. Paste a registered QR code string and press Enter, or use a USB keyboard/HID scanner with an Enter suffix. Whole-stack mode applies a member scan to the complete registered group.
4. Choose **OUT** to remove a stack. Before 24 hours, the system requires acknowledgement and records it. Select specimen mode for an exact partial movement. A mixed stack requires explicit specimen selection; the app never silently moves a guessed subset. Successful specimen movements return the interface to stack mode.
5. Open **Specimen records** to inspect sessions, search/filter records, reprint identities, download audit CSV, or open the printable report. Open **Supervisor** for corrections and eligible undo. A reason is required; original audit events remain visible.

CSV input is UTF-8, with these exact headers:

```csv
project_number,material_ref,test_code,preconditioning_code,specimen_count
000000001234,COTTON-A,ASTM-D5034,AR,20
```

Each row is one stack, with 1–500 specimens; uploads support up to 100 rows and must be below 2 MB. The sample file deliberately contains two materials with the same project/test/preconditioning and an alphanumeric project number. Importing a CSV creates ordinary records; only the explicit seed creates records marked DEMO. Commit retries reuse the same draft's identities, but deliberately importing a file as a new draft creates a new batch.

The latest outline's outage behavior is implemented: **NOT AVAILABLE** disables movements and there is no offline queue. Green confirmation follows a confirmed database commit. If a response is lost, the UI preserves the request ID and reconciles/retries that same request, preventing duplicate movements. Routine IN/OUT requests are explicit rather than automatic second-scan toggles.

## Room rules and audit meaning

| Station | Room | Temperature | Relative humidity | Required residence |
| --- | --- | --- | --- | --- |
| `ppe-door` | PPE | 23 °C ±3 °C | 65% ±5% | 24 hours |
| `shared-door` | Shared | 23 °C ±1 °C | 65% ±2% | 24 hours |
| `footwear-door` | Footwear | 21 °C ±3 °C | 50% ±5% | 24 hours |

Timestamps are stored in UTC and displayed in `America/New_York`. A new entry opens a new continuous conditioning session; gaps between sessions are not added together. Early removal is permitted only after acknowledgement. The elapsed time records residence; it does not measure room temperature/humidity or certify a test's environmental compliance.

Stack membership is fixed in this version. Exact partial movement is supported without creating new child-stack identities. Corrected effective timestamps are distinct from when a supervisor recorded the correction. Undo is allowed only when no subsequent movement/correction changed its affected specimens.

## Development and checks

Backend integration tests run against the dedicated `conditioning_test` PostgreSQL database:

```sh
./scripts/test-backend.sh
```

The script creates that isolated database if absent and passes `TEST_DATABASE_URL`; tests may clear its contents. Never point the test setting at the ordinary application database.

Native development is an alternative to the Docker app services. From the project root, free their ports while keeping PostgreSQL running:

```sh
docker compose stop backend frontend
docker compose up -d db
```

Create a Python 3.12 virtual environment, install `backend/requirements.txt`, set the environment below, and run the commands from `backend/`:

```sh
export DATABASE_URL=postgresql+psycopg://conditioning:conditioning@127.0.0.1:5432/conditioning
export SUPERVISOR_USERNAME=supervisor
export SUPERVISOR_PASSWORD=classroom-demo
export SESSION_SECRET=classroom-demo-change-this-session-secret
alembic upgrade head
uvicorn app.main:app --reload
```

In another terminal, run from `frontend/`:

```sh
npm ci
npm run dev
```

Vite proxies `/api` to the local backend. Browser tests create new batches and movements. Use a separate database and backend so they do not add test records to the normal application. Prepare the database from the project root, then start the isolated backend from `backend/` in its own terminal:

```sh
./scripts/prepare-browser-db.sh
```

```sh
export DATABASE_URL=postgresql+psycopg://conditioning:conditioning@127.0.0.1:5432/conditioning_browser_test
export SUPERVISOR_USERNAME=supervisor
export SUPERVISOR_PASSWORD=classroom-demo
export SESSION_SECRET=classroom-demo-browser-session-secret
alembic upgrade head
uvicorn app.main:app --port 8001
```

Stop any development Vite server on port 5173 first, then run these commands from `frontend/` in another terminal. Playwright starts its own Vite server with the API proxy targeting the isolated backend; it refuses to reuse an existing server:

```sh
npm ci
npx playwright install chromium
API_URL=http://127.0.0.1:8001 npm run test:e2e
npm run build
```

`PLAYWRIGHT_BASE_URL` can target an already-running frontend; use it only with a disposable test deployment. The separate browser database retains its test history when the test server exits.

The integration checks cover fixed-stack movement, exact partial exits, mixed groups, early-exit acknowledgement, repeated-scan no-ops, stale previews, concurrent requests, idempotent retries, room consistency, supervisor corrections, protected audit history, and exported identity/history. The browser checks exercise the visible workflow and unavailable-server behavior. Physical scan speed, first-read accuracy, actual label stock/printer behavior, and Pi boot remain separate acceptance checks.

Verified on October 2, 2026: all 18 backend tests passed inside Docker against PostgreSQL 18, and all 9 Chromium browser tests passed against the isolated browser database. A 20-specimen whole-stack scan reached visible database confirmation in 96 ms against the one-second target in the local browser test (Vite/native FastAPI with Docker PostgreSQL). All 21 rendered demo QR labels, including the stack header, decoded to their registered IDs. Printable report layout and a backup/restore into a new database were also checked. These timings and rendered-label checks do not substitute for physical scanner, printer, or Pi acceptance testing.

For a database backup and a restore into a **new**, explicitly named database:

```sh
mkdir -p backups
./scripts/backup.sh backups/conditioning-2026-10-02.dump
./scripts/restore.sh backups/conditioning-2026-10-02.dump conditioning_restore_20261002
```

Backup refuses an existing output filename; restore refuses an existing target database and does not clean/drop anything. Restored data does not automatically replace the running application's database. Long-term retention requires scheduled backups and verified restore practices outside this classroom prototype.

## Team interfaces and physical deployment

| Component | Responsibility | Boundary |
| --- | --- | --- |
| Label preparation | Review/import prep rows; register identities; produce PDF/CSV labels | Uses registered backend IDs; never invents new identities during reprint |
| Backend | PostgreSQL sessions, transactions, idempotency, corrections, audit exports | FastAPI `/api`, documented at `/docs` |
| Frontend | Room station, preparation, records, supervisor UI | Same-origin `/api`; confirms committed results and manages unknown outcomes |
| Physical station | Scanner, touchscreen, Pi mounting, power/boot | HID scanner with Enter suffix; local compiled UI plus central API proxy |

See [deploy/pi/README.md](deploy/pi/README.md) for Raspberry Pi OS Desktop/labwc provisioning. The local nginx server starts even when the central API/DNS is unavailable; a monitored Chromium service opens the station UI after graphical login and restarts the browser after a crash.

The unspecified old DYMO model has not been verified, so this version supplies printer-independent label PDF/CSV rather than a hardware driver. PDF prep-sheet extraction, RFID, automatic doorway detection, stack regrouping, institutional identity integration, environmental sensors, and writing back to Word project sheets are outside the current prototype. Institutional deployment and the memo's physical accuracy/retention targets still need validation.

The PostgreSQL 18 volume location follows the [official Docker image's PGDATA guidance](https://hub.docker.com/_/postgres). Pi graphical startup follows [Raspberry Pi's official kiosk tutorial](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/), with this project's local UI and supervised restart additions.
