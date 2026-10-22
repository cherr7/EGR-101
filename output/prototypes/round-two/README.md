# Round two: room scanner layouts and desktop transaction-log layouts

Open http://localhost:8090/round-two/ with the existing gallery server:

    ./output/prototypes/serve.sh

These are two independently selectable frontend design sets. They use local simulated records and make no API or database requests. The original 40-concept gallery and tracker application remain separate.

## Scanner

Eight layouts share persistent IN/OUT and whole-stack/individual scope. Six submit immediately on scanning; Review card and Review bar look up a stack before an explicit Record action.

- The product screen is 800 × 480. Header, direction controls, scope controls and input footer remain outside the scrolling middle. Selection and early-checkout dialogs also have separate scrolling bodies and visible action footers.
- Stack A has 10 specimens, B has 20, C has 6. These are variable registered counts, not a universal stack size of 20.
- Whole-stack OUT moves the specimens still inside. After 01–03 leave A, its next whole-stack OUT moves 04–10 automatically. This explicitly replaces round one's mixed-stack selection requirement.
- Whole-stack IN moves specimens currently outside; specimens already inside keep their entry times.
- Individual mode uses numbered checkboxes in a popup. A shared stack QR identifies the stack; numbers identify the selected specimens. Unique specimen QR codes are also supported.
- Repeated movements are no-ops. Confirmations clear after three seconds while a compact last-result label and recent history remain.
- Below-24-hour OUT opens an override popup listing affected specimens and elapsed hours.
- Unavailability blocks submission. A simulated lost response is reconciled using the same request; the pending state cannot create another movement.
- The optional chime is an original two-note sound, played after a confirmed demo movement.

The station.html page is the bottom-dock layout without surrounding review controls. It fits an actual 800 × 480 browser viewport and starts with the label input focused. USB keyboard-style scanner input terminated by Enter works. The database and confirmations are simulated; this page is not a connected tracker client.

The demo-labels.html page has large screen QR codes and 36 numbered 54 × 25 mm labels for letter-size printing. Choose shared stack QR or unique specimen QR. Images have a four-module quiet zone. All IDs are fake prototype IDs.

## Transaction log

Six desktop layouts share one-row editing: table/popup, side inspector, inline editor, direct cells, expanded specimen rows, and table/JSON.

The initial example T004 says A's ten specimens left at 10:10. Edit its selection to 01–03: seven remain inside with their original entry times. Counts derive from numbered specimen IDs; the prep count stays ten. Cancel T004: all ten remain inside.

Time, room, movement direction and optional notes can also be edited. There are no login, approval or mandatory-reason screens. Record missed scan records a manually claimed movement; it does not add specimens to a stack.

The Movements view shows the current interpretation. All transactions additionally shows modifications and cancellations. Each successful edit appends a simple modification referencing the original movement. This is the proposed JSON-log shape for a later audit generator, not a backend migration:

    {
      "id": "T007",
      "type": "modify_movement",
      "target_id": "T004",
      "recorded_at": "2026-10-08T10:40",
      "changes": {"specimens": [1, 2, 3]}
    }

One stack IN transaction carrying ten IDs expands to ten specimen observations. A matching OUT expands to ten more. The batch transaction view does not remove individual histories.

Dates and times in these fixtures use a fixed local demo clock (October 8 at 10:40), rather than a production API timestamp convention.

## Rebuild and verification

    .venv/bin/python output/prototypes/round-two/build.py
    node output/prototypes/round-two/check.mjs

The build uses the installed visualization preview renderer and the project's QR library. Checks use the existing frontend Playwright installation and write verification.json and review screenshots. They exercise layouts, fixed toolbars, partial movements, automatic remaining-specimen checkout, early override, repeated scans, response reconciliation, editing/cancellation, JSON modifications, and real keyboard input into the isolated station.

Scanner speed, physical touchscreen handling, QR readability on the purchased scanner, and final frontend/API integration remain device/integration checks.
