# UI prototype gallery

**Current focused prototypes:** [integer-ID scanner and popup editor](round-three/), with one conditioning period per specimen, separate readiness feedback, log-only scrolling, and whole-transaction Undo.

**Latest round:** [8 fixed-size scanner layouts and 6 independent desktop transaction-log layouts](round-two/), based on the October 8 feedback. The original exploration below is retained for reference.

40 interactive alternatives: **16 scanner workflows, 16 correction workflows, and 8 arrangements of the lab's tools**. This is a separate design exploration. The tracker application, database, and Docker configuration are untouched.

## Open

From the project root:

```sh
./output/prototypes/serve.sh
```

Open **http://localhost:8090**. The combined explorer is **http://localhost:8090/previews/explorer.html**. Set `PROTOTYPE_PORT=8091` to use another port. Docker and the tracker backend are not needed.

Use the three tabs to explore scanning, corrections, and tool arrangements. The named picker below scanner/correction examples selects any of the sixteen designs. Their external scenario controls simulate ordinary work and failures. Reset controls start another local trial. The gallery catalogue explains benefits, risks, backend dependencies, and trial steps; pin concepts to keep a browser-local shortlist.

All specimen data, timestamps, server confirmations, badges, request tickets, and approvals are simulated. The handheld camera does not access a camera. Physical buttons and separate scanners are represented by labelled simulator controls. A simulated green receipt does not write to PostgreSQL.

## Compare

[DESIGN-GUIDE.md](DESIGN-GUIDE.md) contains functional choices, common trials, the original outline's targets, and all forty concepts. Some alternatives require new policies and backend capabilities; the catalogue identifies these explicitly.

The editable conversation sources are `scanner-designs.html`, `correction-designs.html`, and `interface-landscape.html`. `workflow-explorer-template.html` combines them, and the generated `workflow-explorer.html` is the inline explorer. Do not edit generated preview wrappers directly.

## Rebuild and check

The previews are standalone, including their theme and carousel runtime. Rebuilding requires the installed visualize skill at the location used by `build-previews.py`:

```sh
python3 output/prototypes/build-previews.py
node output/prototypes/check-prototypes.mjs
```

The check command uses the project's existing frontend Playwright installation and a running gallery server. `PROTOTYPE_BASE_URL` can select another preview port. It writes review screenshots and `verification.json`.

Verified October 7, 2026: all sixteen scanner primary flows and relevant movement guards; all sixteen correction primary flows, validation, and history preservation; 160 arrangement/failure/task combinations; 132 layout checks at 320, 736, and 1024 pixel content widths; light/dark appearance; typed scanner Enter handling; state preservation across designs/tabs; and shortlist persistence. Final root checks observed zero JavaScript errors and zero live API requests. Actual device height, scanner/printer behavior, performance, authorization, and backend extensions need validation after selecting a direction.
