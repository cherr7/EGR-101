# Code audit and interface update

October 22, 2026. The working tree was compared with the remote repository, upstream main was fast-forwarded through `45c3747`, and the implementation was reviewed with PostgreSQL and real HTTP/browser regressions. This is a focused audit, not a proof that all possible errors are absent.

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

The separate `origin/pi-direct-scanner-input` branch was inspected but not merged. Its scan stream broadcasts a physical scan to every connected page, so two clients with different room/direction settings could compete to record it. Its age check also compares browser and Pi clocks directly. Adoption needs a single active scanner consumer and clock-independent delivery/recovery behavior. These findings concern that unmerged branch, not the shipped keyboard scanner path.

## Evidence and limits

- 61 PostgreSQL tests passed.
- All 25 real-API browser tests passed, including lost prep-save and lost restore responses.
- 8 Mac packaging checks and 23 Pi/NFC/watchdog unit checks passed; the compiled Pi interface passed its headless offline-start smoke check.
- A 20-specimen automatic scan reached green confirmation in 141 ms on the development laptop.
- Real workbook verification covered 28 worksheets, 842 review candidates, 709 registered test specimens, and 709 original plus 709 reprint Data Matrix decodes. Missing identifiers/counts were explicit test placeholders in an isolated database, not invented production data.

Browser print tests intercept printer submission; they do not send physical jobs. Source workbooks and generated private reports remain outside Git. Printer readability, actual scanner output, Pi reboot/focus recovery, Mac unattended startup, and external backup recovery still require installation checks. Full specimen/stack inventories remain browser-filtered and need server pagination if volumes grow substantially.

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for the current workflow and [EXCEL_PIPELINE_VERIFICATION.md](EXCEL_PIPELINE_VERIFICATION.md) for the exact source-file evidence.
