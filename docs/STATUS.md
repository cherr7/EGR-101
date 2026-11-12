# Project status

Last updated November 12, 2026.

## What works

- **Mac host:** `/Applications/Fabric Conditioning.app` (menu bar) registers a boot service that runs PostgreSQL, FastAPI, the staff web app and the outside link. Updates come from release tags with a backup and rollback; the Pis then update themselves.
- **Staff app:** Dashboard, New batch (upload → check the sheet → print → track), Specimens, Transactions, Backups and System health. The prep sheet is checked in the sheet's own layout, and unfinished edits survive reloads. Printed stacks can be edited (fix fields, add or remove specimens) without invalidating labels.
- **Keys:** every key is a sequential number; scan and print retry IDs are `<station>-<n>` and `print-<n>`. See [DATABASE.md](DATABASE.md).
- **Excel import:** blank templates and filled-in job prep sheets (2112, 1506, 1850, 1970 families), including sample IDs from Comments, unordered template rows, shared-sample warnings and implied spares. See [EXCEL_IMPORT.md](EXCEL_IMPORT.md).
- **Labels:** sequential specimen numbers with a Damm check digit in a 16 × 16 Data Matrix, laid out for 62 × 29 mm DK-1209, printed through the Mac's CUPS queue. Preview/print up to 1,000 stacks or 5,000 labels at once.
- **Pi stations:** local React scanner, direct USB input with single-page ownership, optional PN532 NFC, watchdog, Offline display with no offline writes.
- **Backups:** daily checkpoints, extra copy destination, staged validation and explicit restore.

## Verification

- Nov 12, 2026, after merging the dashboard/New batch work with sequential IDs:
  - 117 PostgreSQL backend tests,
  - 53 browser tests, including the kiosk button suite that taps every scanner control at 1280 × 720,
  - 71 Pi tests plus the station package check,
  - 8 Mac packaging tests.
- Real-workbook verification: 13 workbooks, 64 worksheets, 491 prep rows compared with source cells, 516 registered stacks, 2,697 labels decoded twice (before the sheet-row format). Rerun on Nov 12 with the sheet-row format on the two templates on this Mac: 112 rows, 707 labels decoded twice. See [EXCEL_IMPORT.md](EXCEL_IMPORT.md).
- Testing Plan Tests 1–4 passed (scan reliability 1.30 s/scan, p95 confirmation ≤ 20 ms, 0 audit mismatches, UDS 4). See [TEST_RESULTS.md](TEST_RESULTS.md).
- Physical Eyoyo checks: eight exact Data Matrix payloads and a 12-piece IN → partial OUT → remaining OUT → Undo workflow.

## Still to check on real hardware

- **Printer:** add the QL-810W queue on the host Mac; print on a 62 mm roll; confirm size, scale and one cut per label (AirPrint vs Brother driver); scan printed labels; adhesion through lab handling. See [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md).
- **Test 5, outage resilience:** recovery under 60 s after power + network reset (not yet run).
- **Pi:** cold-boot timing, Mac-unavailable boot, USB unplug/replug, focus/freeze recovery on each station.
- **Mac:** approve the boot service in Login Items; check a reboot without login, process recovery, sleep settings, LAN access.
- **Backups:** choose an off-Mac destination and rehearse a restore on another installation.
- **Import:** one real filled order checked with lab staff against the physical grouping.

## Useful improvements not built yet

- Server-side pagination for the full specimen and stack lists at large volumes (transactions already filter on the server).
- A maintenance screen: station heartbeat/version, printer queue state, backup age, logs.
- Microsoft 365 folder/email intake and confirmed SharePoint upload.
- Optional Word datasheet completion.

Training management, scheduling, test results, extra conditioning cycles and offline transactions are outside the agreed scope.

## Audit history

The October 22–23, 2026 code audit compared the working tree with upstream, then reviewed the implementation with PostgreSQL and real HTTP/browser regressions. It was a focused audit, not proof that no errors remain.

### Defects fixed in the October 2026 audit

| Area | Failure found | Resulting behavior |
| --- | --- | --- |
| Prep review | Reload/navigation lost unsaved edits; JSON object key order could also look like a conflicting review. | Quiet local review recovery and order-independent comparison preserve the edited fields. |
| Concurrent prep | A second browser could change the rows between review/save/registration. | Row-locked compare-and-swap checks reject changed reviews atomically; retrying a completed registration returns its existing IDs. |
| Corrections | A popup could pair an old event with a fresh stack version, overwriting someone else's edit. | Event values and version are fetched together and stale saves are rejected. |
| Checkout | Previously loaded session objects could retain stale room/time values after a lock. | Locked reads refresh current values; checkout enforces the effective room and valid chronology. Omitted correction fields retain their original values. |
| Label scope | Preview could show one stack while printing included more. | The same explicit specimen selection drives preview and print, including replacement labels. Original numbering and IDs remain unchanged. |
| Lost print response | A status lookup returning 404 while the original request was still rendering could lead to a new job ID and duplicate labels. | Recovery retries the saved original body and request ID (now `print-<n>`). A durable submission record precedes the printer call; ambiguous submissions are not silently sent again. |
| Transaction history | Limits were not enforced and filtering after a limit could hide matching events. | Validated limits and project/room/station/type filters run before limiting; readable context and direct event lookup are available. |
| Backup maintenance | Cancellation, corrupt settings, malformed archives, and a narrow database rename/journal failure window could strand recovery. | Maintenance waits reset safely, running restore workers keep the traffic gate, database renames commit atomically, and a durable operation marker reconciles the final result. |
| Lost restore response | The UI could lose the identity of a completed restore. | The pending import ID survives reload, including a lost PostgreSQL COMMIT response, and is reconciled against the transactional database marker through a status endpoint; retry returns the recorded result without replacing the database again. |
| Excel panel counts | Text such as `6 15x15` could be interpreted as dimensions instead of six physical panels. | The explicit leading panel count is used and source text is retained. Ambiguous counts still require review. |
| Activity Undo | Activity rows omitted their stack version, allowing an unseen amendment to be paired with a newer version. | Events carry the displayed stack version; stale Undo requests fail without changes. Lost responses reconcile the selected event, including older eligible activity. |
| New upstream NFC | A truncated NDEF record could decode only a prefix as an ID. | Buffer/TLV/record and text-encoding bounds are checked, with regressions for truncated payloads. |

The backup archive remains a consistent database checkpoint with integrity checks, validation in a separate database, and a retained previous database on restore. A checksum detects corruption; it is not a substitute for retaining an independent copy.

### Pi input integration (PR #2) findings

Upstream main added Pi desktop autologin setup, optional PN532 NFC input, and a barcode acceptance-sheet generator. These changes were preserved. NFC parsing bounds and a supposedly read-only installer check were corrected; the check now parses Python without writing bytecode caches.

PR #2 is now integrated with the current interface. Its original input stream broadcast each physical scan to every connected page; the integrated service accepts one active consumer and refuses competing pages. Full/stale streams close, discovery/open failures retry, and worker failures are supervised. Browser readiness and watchdog focus exemptions require usable input rather than a connected socket. Session IDs, sequence IDs, monotonic timestamps, and bounded browser clock calibration replace wall-clock freshness. Hidden pages and blocked/pending transactions do not submit queued scans. The current barcode validator, room tabs, 12-second results, one-tap Undo, and hardened NFC decoding are retained.

The Linux reader configures monotonic kernel event timestamps before grabbing the scanner, so old input buffered during a process stall is rejected. A stale key-release after a rejected frame cannot poison the next fresh frame. Keyboard capture also latches initial eligibility and room/direction/scope context; an interrupted frame cannot complete under changed settings. Undo recovery queries the exact transaction rather than a limited log page.

Native Chromium testing also established that HTTP 409/502/503 can permanently close EventSource. The browser adapter explicitly recreates closed streams, preserving automatic recovery when the input service starts later or another page releases ownership. A native transport regression covers this, in addition to the real-API workflow cases.

The installer safely migrates the old NFC process/runtime into the shared service, preserves scanner.env, and reloads changed user units. A self-contained startup page replaces the silent pre-browser wait for local nginx. It does not wait for the Mac API. This is a software startup improvement, not evidence of measured Pi boot performance.
