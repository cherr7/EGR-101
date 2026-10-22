# Focused frontend prototypes

These demos incorporate the latest scanner, transaction-editor and stack-overview feedback. They live separately from the older application in the repository. All three use the same local mock API model with independent demo state; none writes to the real database.

The dedicated [scanner implementation guide](../../../docs/SCANNER_IMPLEMENTATION_GUIDE.md) covers binary keyboard capture, API integration, Pi kiosk startup, focus recovery, maintenance and physical acceptance. Concrete deployment files are in deploy/scanner-kiosk.

Open the gallery at http://localhost:8090/round-three/ while the prototype server is running.

    ./output/prototypes/serve.sh

## Current behavior

- Each unsigned 40-bit integer ID identifies one specimen. Lookup provides its stack and numbered members.
- Each specimen has one ENTRY and one EXIT. Its states are awaiting entry, conditioning, and finished. An ordinary scan never starts a second conditioning period.
- Scanning any member identifies its stack. Whole-stack IN includes members awaiting entry; whole-stack OUT includes members still conditioning. After 3 of 10 leave, a later whole-stack OUT selects the remaining 7 automatically.
- The scanner requires review, then Submit. IN/OUT direction persists. A numbered checkbox popup supports partial movement. After a successful movement, scope returns to whole stack.
- Only the recent log scrolls on the scanner's main screen. The readiness/review area, direction controls, Undo, Submit, room, and connection status stay visible at 800 × 480.
- The readiness area turns green briefly after confirmation. Early OUT opens an override popup. An unavailable database shows red NOT AVAILABLE and disables submission.
- Undo reverses every specimen in the selected transaction. The original observation remains in the log. Later conflicting events prevent the demo from blindly undoing an earlier movement.
- The desktop frontend lists transactions and specimen records. Editing opens a popup for numbered members, movement time, room, and optional notes. Cancel movement reverses the transaction. Corrections and cancellations remain visible as log entries. No login or mandatory reason is included.
- The read-only stack overview lists all groups, brings ready specimens to the top, and sorts by current time inside. Partial readiness, removed members and different entry times are shown explicitly. Room/status filters, search and a specimen detail popup are available.

## Frontend API boundary

The API names in demo-api.js illustrate operations; they do not prescribe the real HTTP routes or SQL schema. Replace this adapter when the project's API is available. The frontend needs:

1. Specimen lookup by integer ID, including decoded prep data, stack ID, member numbers and IDs, current ENTRY/EXIT, room, and a version.
2. One atomic movement operation for a chosen set of specimen IDs, with direction, station/room, request ID, version, and early-checkout acknowledgment.
3. Request-status lookup so a lost response can be reconciled using the same request ID.
4. Transaction and specimen-record queries, including current effective values and readable history derived from raw_json_time.
5. Transaction correction, whole-transaction undo, and missed-scan recording operations that return confirmed results.
6. Connection/health information.

A stack movement can update many specimen rows. Those rows need a shared transaction reference and original affected ID list so Undo can reverse that entire movement. A correction must update ENTRY/EXIT consistently with the transaction log. The frontend does not implement these guarantees by issuing separate SQL row updates.

Illustrative specimen response:

    {
      "ID": 100000000101,
      "stack_id": "A",
      "specimen_number": 1,
      "ENTRY": "2026-10-07T12:40:00.000Z",
      "EXIT": null,
      "raw_json_time": [
        {
          "transaction_id": "T001",
          "type": "movement",
          "direction": "IN",
          "observed_at": "2026-10-07T12:40:00.000Z",
          "recorded_at": "2026-10-07T12:40:00.000Z"
        }
      ]
    }

The mock stores original events and derives ENTRY/EXIT from their effective state. This demonstrates the UI contract without requiring the real backend to use that implementation. The desktop UI presents readable transactions rather than a raw JSON editor. Claimed movement time and recording time remain distinct for missed scans and corrections.

## Scanner and labels

Binary text + Enter is the default, matching the user's scanner trial. Decimal and hex are explicit development settings. The page-wide listener needs no input focus, consumes scanner Enter before it can activate buttons, rejects blocked scans, and discards malformed or interrupted frames. The manual paste field lives outside the physical station UI.

The Eyoyo scanner's binary text output was confirmed by the user. Full physical label, reboot and recovery testing is still pending. Raw binary bytes are a different transport; the current implementation receives printable binary characters through keyboard events.

labels.html contains on-screen codes and a letter-size printable sheet with 36 unique specimen Data Matrix codes. Each payload is a zero-padded 40-digit binary string; the readable ID is decimal. The generator decodes every code back to its exact registered integer ID. Symbol dimensions are recorded in label-fixtures.json with a one-module quiet zone; no 12 × 12 capacity is assumed. See the [GS1 Data Matrix guideline](https://www.gs1.org/standards/gs1-datamatrix-guideline/25) for symbol sizing reference.

## Build and verification

The builder uses the project's Python environment, Pillow, zxingcpp, and the installed visualization wrapper.

    .venv/bin/python output/prototypes/round-three/build.py
    node output/prototypes/round-three/check.mjs

verification.json records the current model, layout, scanner, editor and overview checks. They cover individual integer IDs, one conditioning period, partial and remaining-member checkout, transaction undo, repeated requests, early override, lost-response reconciliation, unavailable database state, corrections, missed entries, page-wide binary input and readiness counts. The label builder separately decodes all 36 symbols. The kiosk watchdog has its own Python tests:

    .venv/bin/python -m unittest discover -s deploy/scanner-kiosk -p 'test_*.py' -v

These are local simulation checks. The binary keyboard output was reported by the user; label readability, real HTTP/database behavior, actual latency, and physical Pi startup/recovery remain unverified.
