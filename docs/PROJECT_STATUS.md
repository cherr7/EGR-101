# Current project state

Reviewed October 23, 2026 after integrating both Pi hardware PRs with the current interface and recovery fixes. See [CODE_AUDIT.md](CODE_AUDIT.md) for the implementation audit.

## Architecture

The MacBook Neo hosts PostgreSQL, FastAPI, the staff website, and printer submissions. Staff computers and the three Pi room stations use HTTP to reach the same backend. Browsers never connect directly to SQL. The Mac launcher starts the services and opens the staff website; the supplied LaunchDaemon installer supports startup before interactive login once installed on the intended host.

Pi stations keep the scanner interface locally, so they can display **Offline** even when the Mac is unreachable after a restart. They accept no offline transactions. The local input service reads the USB Eyoyo directly and streams frames to one active Chromium page. Optional PN532 NFC input joins the same service. The Pi keeps Chromium kiosk mode on Raspberry Pi OS Desktop; the Mac app, database, and staff interface remain the same architecture.

## Current interfaces

| Screen | What staff do there |
| --- | --- |
| Stacks | Find ready stacks first, filter by project/room/status, see scoped counts and time remaining, and open stack details. |
| Specimens → Prepare / import | Upload Excel/CSV or enter preparation rows, review source cells, resolve missing fields, register the pieces, and preview/print their labels. Unfinished review edits survive navigation/reload locally without an extra Save step. |
| Specimens → Registered specimens | Search numbered pieces, inspect an individual history, select existing labels for replacement, open stack details, and export records/reports. |
| Transactions | Search observations and changes; edit time, room, numbered selection, or notes; record a missed scan; undo an eligible transaction. A before/after preview shows the proposed effect. |
| Backups | See checkpoint/scheduler/copy health, create/download a checkpoint, configure an additional destination, validate an imported ZIP, and explicitly restore it. |
| Room scanner | Choose PPE, Shared, or Footwear; set persistent IN/OUT; scan for automatic recording; optionally choose numbered pieces in a popup. The fixed dock contains IN, OUT, and one-tap Undo. |

The scanner header contains room buttons, Stack / Individual, and Online / Offline. The equal-width dock contains IN, OUT and Undo. Green lasts 12 seconds; Activity updates immediately after confirmation and retains the scan afterward. Ordinary error/duplicate notices clear after four seconds without delaying the next scan. Offline, unavailable input and uncertain transaction results remain visible until resolved. Undo targets the top scan, marks it Undone, and disables without moving to an older row. Only Activity scrolls; popup actions remain visible. There is no room-screen sound toggle or Redo.

Every physical specimen has one permanent 128-bit ID. Its Data Matrix carries only that identifier as 39 decimal digits. Stack membership comes from the backend. Whole-stack OUT moves the remaining inside specimens: after three of ten leave, the next whole-stack OUT moves seven. Reprints preserve identities. Each specimen has one effective ENTRY and EXIT, with readiness based on the designated 24-hour room visit. Original observations and subsequent corrections remain in history, without requiring supervisor roles or reasons.

## Verification completed

- 62 PostgreSQL integration tests passed, including concurrency, stale corrections, prep review conflicts, idempotency, selective printing, and backup recovery.
- All 44 browser checks passed, including real-API direct-input movements, lost movement/Undo responses, stale/hidden/duplicate frames, and native EventSource unavailable/occupied recovery. A local 20-specimen keyboard scan took 118 ms from final Enter to green confirmation.
- 8 Mac packaging checks and 59 Pi package/NFC/watchdog checks passed. The compiled Pi package also passed its headless readiness, single-owner, three-room, and startup-page workflows.
- All 28 worksheets in the four supplied workbooks were inspected. Reviewed test registrations produced 709 label PDFs and 709 reprints; every embedded Data Matrix decoded to its exact registered ID. See [the detailed Excel verification](EXCEL_PIPELINE_VERIFICATION.md).

These are software checks. The Excel templates still need actual material/project fields where missing, and aggregate prep-board quantities still require an explicitly reviewed physical count. Training/reference documents are not specimen orders. The verification used isolated, marked test databases; the normal fresh app dataset was not populated with those source documents.

## Deployment and physical checks still required

- Use one real filled order with lab staff to confirm the physical grouping and imported values.
- Configure the Brother macOS queue and actual roll; test 2 × 1 inch sizing, scaling, cutter behavior, fabric adhesion, and first-read performance with the Eyoyo.
- Qualify each Pi's landscape screen, USB reconnect, kiosk focus/freeze recovery, and cold boot with the backend unavailable.
- Install the Mac startup service on the intended host and verify reboot/process recovery, awake settings, LAN access, and FileVault behavior.
- Choose an off-host checkpoint destination and rehearse restore on another installation. A verified local folder copy does not establish a completed SharePoint upload.

Old prototype galleries, outdated screenshots, the unused supervisor screen, and the superseded Pi deployment were removed. Current screenshots can be regenerated from isolated DEMO records with `node scripts/capture-ui.mjs`; see [output/README.md](../output/README.md).

## Useful improvements not built yet

- Server-side pagination for the full specimen and stack inventories at large lab volumes. Transaction filtering already runs on the server.
- A maintenance screen showing station heartbeat/version, printer queue status, backup age, and service logs. Queue acceptance currently does not prove physical print completion.
- Approved Microsoft 365 folder/email intake and managed SharePoint upload verification.
- Optional Word datasheet completion from the existing independent reports.

Training management, scheduling, test results, extra conditioning cycles, and offline transactions are outside the agreed core. Direct input from the Pi PR is integrated with single-page ownership, closed stale/full streams, discovery and worker recovery, calibrated monotonic freshness, and separate reader readiness. Startup feedback is implemented as a self-contained local page; physical timing remains unmeasured.

## Operational guides

- [Main README](../README.md)
- [Code audit](CODE_AUDIT.md)
- [Excel pipeline verification](EXCEL_PIPELINE_VERIFICATION.md)
- [Scanner implementation](SCANNER_IMPLEMENTATION_GUIDE.md)
- [Barcode and printer](BARCODE_AND_PRINTER.md)
- [Backup recovery](BACKUPS.md)
- [Mac host setup](../deploy/macos/README.md)

## Activity and room inventory

The scanner divides its main screen equally between the result and Activity. The upper-right expand button opens detailed Activity with a minus button for each movement; minus reverses that exact transaction, preserves its audit trail, and is disabled after Undo or intervening activity. The dock still targets only the newest movement. Inventory opens a read-only popup for the selected room, defaulting to Ready, with Inside, Next 2h, and project filters. Rows show ready/inside counts, elapsed time, and expandable specimen numbers. Inventory refreshes every ten seconds, updates readiness against server time, and clears stale results on failure. Scans are discarded while either popup is open; close it before scanning. Only the list scrolls, leaving filters and Close visible.
