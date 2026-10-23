# Current application outputs

The active application is built from `frontend/src` and `backend/app`. Open the Mac-hosted staff app at http://127.0.0.1:8120 or the room interface at http://127.0.0.1:8120/kiosk.

- `macOS/`: generated Mac app launcher and deployment plan.
- `station-ui/`: generated local Pi kiosk package.
- `screenshots/`: freshly captured current interface previews, generated locally and excluded from Git.
- `qa/`: local verification artifacts and disposable checkpoint exports, excluded from Git.

Old design galleries, mock interfaces, barcode images, and obsolete screenshots were removed. Their prior versions remain in Git history. They are not part of the running app.

To regenerate current captures, first run an isolated API backed by `fabric_browser_test` on loopback port 8121, build the frontend, then run:

    node scripts/capture-ui.mjs

The capture script requires the frontend Playwright dependency and Google Chrome. It creates clearly marked synthetic DEMO records only on that isolated local API and refuses other origins. It never submits a print job. The generated manifest records the capture time and test-data source.
