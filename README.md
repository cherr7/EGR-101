# Fabric Conditioning Tracker

The working application runs on one MacBook Neo: PostgreSQL, the FastAPI HTTP API, the React staff app, and label-print submission. Staff computers and three Raspberry Pi scanner stations use that same database through the API. The scanner is a separate fullscreen interface; its Pi deployment keeps its own compiled UI locally.

The current workflow gives **every physical specimen a permanent sequential number**, linked to a stack. Labels encode only that number plus a check digit, in a 16 × 16 ECC200 Data Matrix. Each specimen has one effective ENTRY and one EXIT for the designated 24-hour conditioning step. Obsolete galleries and screenshots have been removed. The active interfaces are built from frontend/src; fresh local captures are described in output/README.md.

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

The staff app opens on the **Dashboard**. New work enters through **New batch**, a four-step flow that closes the loop back to the dashboard. One prep sheet makes one **batch**.

1. **Upload prep sheet:** drop an Excel .xlsx/.xlsm or CSV file, or enter stacks manually. The importer inspects every worksheet, keeps source metadata and warnings, and never runs macros. Nothing is registered yet.
2. **Confirm stacks:** each row is one physical group: one material/sample, test, and exact treatment text. Include every piece sent to conditioning, extras too, and split combined groups first. Fill any missing values (the "rows need values" filter jumps to them). **Register** creates the stacks and their permanently numbered specimens. Repeating the same registration returns the existing batch, and registered rows can't be edited afterward. Extra pieces become a new batch.
3. **Print labels:** preview the actual labels and print to an installed Mac printer queue. The default is 2 × 1 inch / 50.8 × 25.4 mm; reprints keep the same IDs. Without a printer, open the PDF, print it, and choose **I printed these from the PDF** so the dashboard knows the labels exist. **Print later** leaves the stacks on the dashboard as *Needs labels*.
4. **Track:** a live summary of the batch's pieces: labeled → in room → ready → finished. It links to the batch's view on the dashboard and its transaction history. A batch closes when every piece has been scanned out.

The **Dashboard** refreshes every 15 seconds and can be filtered by room, project, test, treatment, batch, or free text. Filters live in the URL, so a filtered view can be bookmarked or shared. Its sections, most urgent first:

- **Needs attention (pipeline gaps):** pieces registered but never labeled, and pieces labeled but never scanned IN, with how long they've waited. A scan IN proves a label exists. Pieces never scanned in have no room yet, so the room filter doesn't hide them.
- **Batches in progress:** one card per open batch with a stage bar, plus recently completed batches.
- **Ready to remove:** one collapsed list per room ("Shared · 14 stacks · 212 pieces ready · oldest waiting 3 d 4 h") with a breakdown of how long ready pieces have waited (< 1 day, 1–3 days, > 3 days). That shows when finished pieces are piling up. Expanded, it lists stacks longest-waiting first.

Other pages:

- **Specimens:** inspect each numbered specimen's single ENTRY/EXIT pair and history. Download session CSV, audit CSV, or a printable conditioning report.
- **Transactions:** amend a scan's selected specimen numbers, time, room, or optional notes; record a missed movement; or undo an eligible transaction. Filter to one stack (`#transactions?stack=ID`) or one batch (`#transactions?batch=ID`). Original observations remain in the append-only audit history.
- **Backups** and **System health:** see [the backup guide](docs/BACKUPS.md) and [the system health guide](docs/SYSTEM_HEALTH.md).

Older links still work: `#overview` opens the dashboard and `#prep` opens New batch.

Project numbers remain strings, preserving letters and leading zeroes. They are separate from generated specimen/stack IDs. Treatment wording is copied from the prep sheet; AR, 5L, UV, CH, or other wording describes separate groups of physical pieces, not repeated conditioning cycles for the same specimen.

Registration allows 1–500 specimens per stack; 20 is typical rather than a hard maximum. Workbook rows are proposals that require review, not proof of how a physical pile has been divided. Additional specimens are registered as a new group rather than appended to an existing stack.

## Room scanner

Open **http://MACBOOK-HOSTNAME.local:8120/kiosk?station=shared-door** for a hosted station, or deploy the locally served Pi package described in the [scanner implementation guide](docs/SCANNER_IMPLEMENTATION_GUIDE.md).

The production station targets the **1280 × 720 landscape Touch Display 2** and Eyoyo EYH2 USB scanner. Chromium remains in fullscreen kiosk mode on Raspberry Pi OS Desktop. It needs no visible input box. On installed Pis, a local input service reads the scanner directly and delivers frames to one active page; a usable reader and visible page are required. Hosted scanner pages retain keyboard capture. Choose persistent IN or OUT and scan a Data Matrix: an ordinary whole-stack movement records automatically. There is no routine Submit step.

- Whole-stack IN includes members awaiting their first entry; scanning any member identifies the stack.
- Whole-stack OUT includes only members still inside. If 3 of 10 have left, the next whole-stack OUT moves the remaining 7.
- Individual mode opens numbered checkboxes. Confirming that popup moves exactly those pieces. Stack/Individual stays selected until changed and survives reloads, like IN/OUT.
- An OUT before 24 hours opens an early-checkout popup. Override records the real removal and incomplete conditioning.
- Undo reverses the newest scan shown in Activity. It marks that row Undone and disables, rather than silently targeting another scan. The staff log retains the original and Undo observations.

The header has PPE / Shared / Footwear, Stack / Individual, and green Online / red Offline. The bottom dock has equally sized IN, OUT, and Undo buttons. Green appears only after a confirmed commit and remains for 12 seconds without delaying the next scan. Activity updates immediately on confirmation and retains the scan afterward. Ordinary errors/duplicate notices last four seconds and accept another scan immediately. Offline and unresolved movement/Undo states persist until resolved, with no offline transactions. Repeats preserve original timestamps; finished pieces do not start another conditioning period.

The readiness/result area and bottom controls remain visible; only the recent log scrolls in the main station view. Popup bodies can scroll while their action bars stay visible. Database and input status are separate: an open event stream alone is not scanner readiness. Unsupported, duplicate, stale, and wrong-session input frames are rejected without queuing a movement.

The Pi opens a self-contained **Starting station…** page before its local web service is ready, then loads the scanner interface. It never waits for the Mac API to display the local UI. This improves feedback during startup; actual power-on/Chromium timing still needs hardware measurement. Normal 64-bit Raspberry Pi OS Desktop is the deployment target; Lite is not a drop-in replacement for the supplied graphical-session installer. See the scanner guide for installation, optional NFC, explicit upgrades from the former NFC service, recovery, and hardware checks.

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

- [Code audit and interface update](docs/CODE_AUDIT.md): verified fixes, upstream integration findings, and current UI organization.
- [Real Excel pipeline verification](docs/EXCEL_PIPELINE_VERIFICATION.md): source-file coverage, reviewed missing fields, and decoded labels.
- [Current state and possible improvements](docs/PROJECT_STATUS.md): implemented features, deployment checks, known gaps, and proposed interface priorities.
- [Barcode and Brother printer guide](docs/BARCODE_AND_PRINTER.md): sequential label codes with a check digit, 16 × 16 Data Matrix symbols, the 62 × 25.4 mm Brother QL layout, preview/print, and physical acceptance checks.
- [Backup and restore guide](docs/BACKUPS.md): consistent ZIP checkpoints, SHA-256 validation, daily 2 a.m. Eastern snapshots, 30-day local retention, staged restore, and retained previous databases.
- [Mac host guide](deploy/macos/README.md): local app launcher and system startup installation.
- [Local input protocol](docs/SCANNER_INPUT_PROTOCOL.md): ownership, monotonic freshness, reconnects, reader health, and fallback.
- [Scanner implementation guide](docs/SCANNER_IMPLEMENTATION_GUIDE.md): direct USB input, keyboard fallback, local startup UI, station setup, upgrades, and recovery.

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
    python3 -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py'

Build the local Pi UI package after the frontend build:

    python3 scripts/build-station.py

Build the Mac app again after final source/frontend changes:

    python3 deploy/macos/build_app.py

Automated checks cover identity preservation, one-period movement, partial checkout, stale/concurrent requests, lost-response reconciliation, amendments/undo, audit history, label decoding, imports, exports, and backup validation. Software checks do not establish physical scan accuracy, stock adhesion, print readability, network behavior, or actual Pi/Mac cold-start recovery. Measure confirmed scan latency from the scanner's final Enter to visible confirmation against the outline's one-second target during hardware acceptance.

Verified on October 23, 2026: **62 PostgreSQL integration tests, 44 browser checks, 8 Mac packaging checks, and 59 Pi package/NFC/watchdog checks passed**. Browser checks include real-API movements through simulated Pi transport and native EventSource recovery from unavailable/occupied HTTP responses. The compiled Pi package also passed a headless offline-start workflow. A 20-specimen scan took **118 ms from final Enter to visible green confirmation** on this development laptop. The native launcher and portable command-line checkpoint export were checked against the running host. Physical Eyoyo testing additionally verified eight exact Data Matrix payloads and a 12-piece IN → numbered partial OUT → remaining-stack OUT → Undo workflow. Printer qualification, USB reconnect, and actual Mac/Pi cold-start behavior remain hardware acceptance checks.

The older Docker Compose files and scripts remain for legacy development. They are not the primary Mac deployment documented here, and old prototype payloads or supervisor workflows should not be used as the current specification.

## Activity and room inventory

The scanner divides its main screen equally between the result and Activity. The upper-right expand button opens detailed Activity with a minus button for each movement; minus reverses that exact transaction, preserves its audit trail, and is disabled after Undo or intervening activity. The dock still targets only the newest movement. Inventory opens a read-only popup for the selected room, defaulting to Ready, with Inside, Next 2h, and project filters. Rows show ready/inside counts, elapsed time, and expandable specimen numbers. Inventory refreshes every ten seconds, updates readiness against server time, and clears stale results on failure. Scans are discarded while either popup is open; close it before scanning. Only the list scrolls, leaving filters and Close visible.
