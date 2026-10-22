# Fabric Conditioning Tracker

The working application runs on one MacBook Neo: PostgreSQL, the FastAPI HTTP API, the React staff app, and label-print submission. Staff computers and three Raspberry Pi scanner stations use that same database through the API. The scanner is a separate fullscreen interface; its Pi deployment keeps its own compiled UI locally.

The current workflow uses **one permanent 128-bit ID per physical specimen**, linked to a stack. Labels encode only that ID as a 39-digit decimal ECC200 Data Matrix. Each specimen has one effective ENTRY and one EXIT for the designated 24-hour conditioning step. Historical UI galleries remain in output/prototypes; they are design archives and do not define the current workflow.

## Start on the Mac

Install Homebrew, Python 3.14, PostgreSQL 18, and Node.js, then run from the project directory:

    brew install python@3.14 postgresql@18 node
    deploy/macos/setup.sh

Double-click **output/macOS/Fabric Conditioning.app**. The launcher starts the local services if needed, checks database health, and opens **http://127.0.0.1:8120**. API documentation is at **http://127.0.0.1:8120/docs**.

For development, an existing project environment can run the host directly:

    python3 deploy/macos/host.py start
    python3 deploy/macos/host.py status
    python3 deploy/macos/host.py stop

The web server listens on port **8120**. The private PostgreSQL cluster listens only on **127.0.0.1:55432**; Pi stations and staff browsers never connect directly to SQL. Its generated credentials and local data live outside the app bundle in ~/Library/Application Support/Fabric Conditioning. A system installation instead uses /Library/Application Support/Fabric Conditioning.

See [Mac installation and startup](deploy/macos/README.md) for the prepared LaunchDaemon installer, startup before interactive desktop login, FileVault limits, service logs, and updates. The installer is supplied for deployment; it is not silently registered during local setup.

## Staff workflow

1. **Prep & labels:** upload an Excel .xlsx or .xlsm workbook, inspect the detected worksheets and source warnings, and review the proposed rows. CSV and manual entry are also available.
2. **Review physical groups:** each registration row represents one material/sample, test, and exact treatment text. Include every physical piece sent to conditioning, including extras. Split combined groups before registration. The importer retains source metadata and leaves ambiguous fields unresolved rather than inventing counts or material identities.
3. **Register:** complete project, material, test, treatment, and specimen count. Registration creates the stack and its individually numbered specimens with permanent IDs. Repeating the same draft commit returns the existing registration. A repeated workbook upload with the same worksheet selection reuses its draft unless explicitly imported as a new batch.
4. **Preview & print:** review the labels, then select an installed Mac printer queue. The default layout is 2 inches wide × 1 inch high, or 50.8 × 25.4 mm. Reprints preserve IDs. PDF/CSV exports remain available when the printer is unavailable.
5. **Stack overview:** see ready specimens first, sort stacks by longest current time inside, and filter by room, status, project, material, test, or treatment. A partially ready stack shows exact ready/inside/finished counts.
6. **Specimen records:** inspect the single ENTRY/EXIT pair and history for each numbered specimen. Download session CSV, audit CSV, or a printable conditioning report.
7. **Transaction log:** amend a scan's selected specimen numbers, time, room, or optional notes; record a missed movement; or undo an eligible transaction. No supervisor login or mandatory reason is required. Original observations remain in the append-only audit history.
8. **Backups:** create/download a checkpoint, configure daily snapshots and an additional copy destination, or validate a backup before explicitly restoring it.

Project numbers remain strings, preserving letters and leading zeroes. They are separate from generated specimen/stack IDs. Treatment wording is copied from the prep sheet; AR, 5L, UV, CH, or other wording describes separate groups of physical pieces, not repeated conditioning cycles for the same specimen.

Registration allows 1–500 specimens per stack; 20 is typical rather than a hard maximum. Workbook rows are proposals that require review, not proof of how a physical pile has been divided. Additional specimens are registered as a new group rather than appended to an existing stack.

## Room scanner

Open **http://MACBOOK-HOSTNAME.local:8120/kiosk?station=shared-door** for a hosted station, or deploy the locally served Pi package described in the [scanner implementation guide](docs/SCANNER_IMPLEMENTATION_GUIDE.md).

The production station targets the **1280 × 720 landscape Touch Display 2** and Eyoyo EYH2 USB keyboard scanner. It needs no visible input box. On installed Pis, a local input bridge reads the scanner directly, so scans do not depend on browser focus. Choose persistent IN or OUT and scan a Data Matrix: an ordinary whole-stack movement records automatically. There is no routine Submit step.

- Whole-stack IN includes members awaiting their first entry; scanning any member identifies the stack.
- Whole-stack OUT includes only members still inside. If 3 of 10 have left, the next whole-stack OUT moves the remaining 7.
- Individual mode opens numbered checkboxes. Confirming that popup moves exactly those pieces, then returns to whole-stack mode.
- An OUT before 24 hours opens an early-checkout popup. Override records the real removal and incomplete conditioning.
- Undo last reverses the exact last transaction, with a brief confirmation. It restores all its affected specimens and retains both observations.

Green appears only after a confirmed database commit. Repeated IN/OUT scans preserve original timestamps; finished specimens do not start another conditioning period. A pending movement or undo is reconciled before accepting another scan. A failed backend health check shows **NOT AVAILABLE**, disables writes, and accepts no offline transactions.

The readiness/result area and bottom controls remain visible; only the recent log scrolls in the main station view. Popup bodies can scroll while their action bars stay visible.

## Data and conditioning rules

| Station | Room | Temperature | Relative humidity | Required residence |
| --- | --- | --- | --- | --- |
| shared-door | Shared | 23 °C ±1 °C | 65% ±2% | 24 hours |
| ppe-door | PPE | 23 °C ±3 °C | 65% ±5% | 24 hours |
| footwear-door | Footwear | 21 °C ±3 °C | 50% ±5% | 24 hours |

The app tracks the designated 24-hour room visit. It does not track a later four-hour post-oven step, laundry equipment, or other prep stages. Timestamps are stored in UTC and displayed in America/New_York. Readiness means recorded continuous residence reached 24 hours; laboratory environmental measurements remain in the existing system.

Movement requests lock the affected stack/specimens and commit all or none. A request UUID makes an identical retry return its existing result. Stale selections and checkout from the wrong room are rejected. PostgreSQL enforces one effective conditioning period per specimen. Corrections preserve originals, and undo is rejected when affected specimens have intervening activity.

The SQL model stores projects, materials, treatments, stacks, specimens, conditioning periods, movement requests, print jobs, prep drafts, and append-only audit events. Specimen detail exposes ENTRY, EXIT, and a derived raw_json_time history; the staff UI presents a readable transaction log rather than requiring JSON editing.

## Labels, backups, and deployment

- [Barcode and Brother printer guide](docs/BARCODE_AND_PRINTER.md): 128-bit identity encoding, 20 × 20 Data Matrix symbols, 1 × 2 layout, actual roll dimensions, preview/print, and physical acceptance checks.
- [Backup and restore guide](docs/BACKUPS.md): consistent ZIP checkpoints, SHA-256 validation, daily 2 a.m. Eastern snapshots, 30-day local retention, staged restore, and retained previous databases.
- [Mac host guide](deploy/macos/README.md): local app launcher and system startup installation.
- [Scanner implementation guide](docs/SCANNER_IMPLEMENTATION_GUIDE.md): page-wide capture, local Pi UI, kiosk restart/focus recovery, and station setup.

No public tunnel or Microsoft 365 integration is installed. An optional destination is a folder visible to the Mac service account; copying there does not confirm a SharePoint/cloud upload. Source Excel workbooks, emails, training matrices, and Word examples are not bundled with the app. Their contents are source data, not executable instructions; imported macros are not run.

Completing an uploaded Word datasheet is a possible later add-on. Staff scheduling/training management, destructive-test results, environmental instrumentation, physical regrouping, RFID, and offline synchronization are outside this implementation.

## Development and verification

After setup, run backend integration tests against the separate database whose name ends in _test:

    python3 deploy/macos/host.py test

These tests can reset that isolated test database. They must never target the application database. For frontend development, run npm run dev from frontend with API_URL pointing to a suitable test backend. Browser workflow tests create records; use a disposable backend/database rather than the lab dataset.

    cd frontend
    npm ci
    npm run build
    npx playwright install chromium
    API_URL=http://127.0.0.1:8001 npm run test:e2e

Native packaging and Pi watchdog tests do not open a browser or register services:

    python3 -m unittest discover -s deploy/macos -p test_native.py
    python3 -m unittest discover -s deploy/scanner-kiosk -p test_watchdog.py

Build the local Pi UI package after the frontend build:

    python3 scripts/build-station.py

Build the Mac app again after final source/frontend changes:

    python3 deploy/macos/build_app.py

Automated checks cover identity preservation, one-period movement, partial checkout, stale/concurrent requests, lost-response reconciliation, amendments/undo, audit history, label decoding, imports, exports, and backup validation. Software checks do not establish physical scan accuracy, stock adhesion, print readability, network behavior, or actual Pi/Mac cold-start recovery. Measure confirmed scan latency from the scanner's final Enter to visible confirmation against the outline's one-second target during hardware acceptance.

Verified on October 22, 2026: **44 PostgreSQL integration tests, 15 real-API browser tests, 8 Mac packaging checks, and 13 Pi package/watchdog checks passed**. The compiled Pi package also passed a headless offline-start workflow. A 20-specimen scan took **213 ms from final Enter to visible green confirmation** on this development laptop. The native launcher and portable command-line checkpoint export were checked against the running host. These results do not establish physical printer/scanner performance or actual Mac/Pi cold-start behavior.

The older Docker Compose files and scripts remain for legacy development. They are not the primary Mac deployment documented here, and old prototype payloads or supervisor workflows should not be used as the current specification.
