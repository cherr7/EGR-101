# Code audit and interface update

Initial audit October 22, 2026; Pi input integration reviewed October 23, 2026. The working tree was compared with the remote repository, upstream main was fast-forwarded through `45c3747`, and the implementation was reviewed with PostgreSQL and real HTTP/browser regressions. This is a focused audit, not a proof that all possible errors are absent.

## Important defects fixed

| Area | Failure found | Resulting behavior |
| --- | --- | --- |
| Prep review | Reload/navigation lost unsaved edits; JSON object key order could also look like a conflicting review. | Quiet local review recovery and order-independent comparison preserve the edited fields. |
| Concurrent prep | A second browser could change the rows between review/save/registration. | Row-locked compare-and-swap checks reject changed reviews atomically; retrying a completed registration returns its existing IDs. |
| Corrections | A popup could pair an old event with a fresh stack version, overwriting someone else's edit. | Event values and version are fetched together and stale saves are rejected. |
| Checkout | Previously loaded session objects could retain stale room/time values after a lock. | Locked reads refresh current values; checkout enforces the effective room and valid chronology. Omitted correction fields retain their original values. |
| Label scope | Preview could show one stack while printing included more. | The same explicit specimen selection drives preview and print, including replacement labels. Original numbering and IDs remain unchanged. |
| Lost print response | A status lookup returning 404 while the original request was still rendering could lead to a new job ID and duplicate labels. | Recovery retries the saved original body and request UUID. A durable submission record precedes the printer call; ambiguous submissions are not silently sent again. |
| Transaction history | Limits were not enforced and filtering after a limit could hide matching events. | Validated limits and project/room/station/type filters run before limiting; readable context and direct event lookup are available. |
| Backup maintenance | Cancellation, corrupt settings, malformed archives, and a narrow database rename/journal failure window could strand recovery. | Maintenance waits reset safely, running restore workers keep the traffic gate, database renames commit atomically, and a durable operation marker reconciles the final result. |
| Lost restore response | The UI could lose the identity of a completed restore. | The pending import ID survives reload, including a lost PostgreSQL COMMIT response, and is reconciled against the transactional database marker through a status endpoint; retry returns the recorded result without replacing the database again. |
| Excel panel counts | Text such as `6 15x15` could be interpreted as dimensions instead of six physical panels. | The explicit leading panel count is used and source text is retained. Ambiguous counts still require review. |
| New upstream NFC | A truncated NDEF record could decode only a prefix as an ID. | Buffer/TLV/record and text-encoding bounds are checked, with regressions for truncated payloads. |

The backup archive remains a consistent database checkpoint with integrity checks, validation in a separate database, and a retained previous database on restore. A checksum detects corruption; it is not a substitute for retaining an independent copy.

## Interface decisions

The staff navigation now contains Stack overview, Specimens, Transaction log, and Backups. Specimens has Prepare / import and Registered specimens tabs. One shared label dialog works from a newly registered batch, a stack, a selected set of pieces, or a single specimen's history. No extra draft-saving workflow or new permissions were introduced.

The scanner uses three persistent room tabs, a separate ready/result area, whole-stack/individual controls in the main card, and a fixed IN / OUT / Undo dock. Ordinary scans record automatically. Numbered selection and early checkout use popups, with actions kept visible. Green confirmation lasts 12 seconds and does not block another scan. The room log scrolls independently and identifies projects/materials and changes.

## Repository update findings

Upstream main added Pi desktop autologin setup, optional PN532 NFC input, and a barcode acceptance-sheet generator. These changes were preserved. NFC parsing bounds and a supposedly read-only installer check were corrected; the check now parses Python without writing bytecode caches.

PR #2 is now integrated with the current interface. Its original input stream broadcast each physical scan to every connected page; the integrated service accepts one active consumer and refuses competing pages. Full/stale streams close, discovery/open failures retry, and worker failures are supervised. Browser readiness and watchdog focus exemptions require usable input rather than a connected socket. Session IDs, sequence IDs, monotonic timestamps, and bounded browser clock calibration replace wall-clock freshness. Hidden pages and blocked/pending transactions do not submit queued scans. The current barcode validator, room tabs, 12-second results, one-tap Undo, and hardened NFC decoding are retained.

The Linux reader configures monotonic kernel event timestamps before grabbing the scanner, so old input buffered during a process stall is rejected. A stale key-release after a rejected frame cannot poison the next fresh frame. Keyboard capture also latches initial eligibility and room/direction/scope context; an interrupted frame cannot complete under changed settings. Undo recovery queries the exact transaction rather than a limited log page.

Native Chromium testing also established that HTTP 409/502/503 can permanently close EventSource. The browser adapter explicitly recreates closed streams, preserving automatic recovery when the input service starts later or another page releases ownership. A native transport regression covers this, in addition to the real-API workflow cases.

The installer safely migrates the old NFC process/runtime into the shared service, preserves scanner.env, and reloads changed user units. A self-contained startup page replaces the silent pre-browser wait for local nginx. It does not wait for the Mac API. This is a software startup improvement, not evidence of measured Pi boot performance.

## Evidence and limits

- 61 PostgreSQL tests passed.
- All 36 browser checks passed: real-API direct/keyboard workflows plus native HTTP EventSource recovery and interface checks.
- 8 Mac packaging checks and 53 Pi/NFC/watchdog checks passed; compiled-package smoke checks cover single ownership, selected PPE room, usable-reader heartbeat, unavailable boot, and the standalone starting screen.
- A local 20-specimen keyboard scan reached green in 111 ms. This does not measure physical evdev/Pi/printer timing.
- Real workbook verification covered 28 worksheets, 842 review candidates, 709 registered test specimens, and 709 original plus 709 reprint Data Matrix decodes. Missing identifiers/counts were explicit test placeholders in an isolated database, not invented production data.

Browser print tests intercept printer submission; they do not send physical jobs. Source workbooks and generated private reports remain outside Git. Printer readability, actual scanner output, Pi reboot/focus recovery, Mac unattended startup, and external backup recovery still require installation checks. Full specimen/stack inventories remain browser-filtered and need server pagination if volumes grow substantially.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the current workflow and [EXCEL_PIPELINE_VERIFICATION.md](EXCEL_PIPELINE_VERIFICATION.md) for the exact source-file evidence.
