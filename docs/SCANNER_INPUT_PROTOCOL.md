# Local scanner input contract

The Pi input service is hardware transport, not another database/backend. Its loopback HTTP server listens on 8766. nginx exposes only the same-origin station routes below. The React page still uses the central FastAPI for lookup, atomic movements, Undo, and request reconciliation. This contract documents the integrated USB/NFC service and browser adapter.

## Endpoints and ownership

| Pi browser path | Service path | Behavior |
| --- | --- | --- |
| GET /station/input/events | GET /events | SSE for one active page. HTTP 409 if another connection owns input. No event replay. |
| GET /station/input/status | GET /status | No-cache JSON status and monotonic clock sample. Does not claim the scanner or record movements. |
| GET /station/ready | nginx local file check | Read-only local-page readiness for the file-origin startup screen. No database requirement or credentialed API CORS. |
| POST /station/heartbeat | watchdog /heartbeat on 8765 | Small page-health payload, independent of central database availability. |

The first active stream wins. The service does not broadcast physical scans. A closed client is detected through stream writes/one-second keepalives; its connection releases ownership. A stalled socket has a finite write timeout. An overflowing bounded stream receives a terminal error and closes, discarding queued frames. The browser retries closed HTTP error responses explicitly as well as handling ordinary EventSource reconnects. A second page cannot use keyboard fallback to bypass known ownership.

This is a simple connection guard, not an account system, distributed lease, or persistent transaction queue. Movement UUIDs remain the authoritative backend retry identities; local stream IDs are not SQL specimen IDs or movement request UUIDs.

## Status and events

GET status includes:

- session_id: random process UUID; changes after service restart.
- now_ms: service CLOCK_MONOTONIC time in milliseconds.
- scanner: starting, ok, missing, or error; scanner_name and scanner_error explain device state.
- nfc: off, starting, ok, or error; nfc_error explains optional reader failure.
- pages: zero or one active event-stream consumer.
- local_ips: station interface addresses, read without contacting the central API; the UI displays IPv4 addresses for debugging.

The initial SSE status message and later status changes carry kind=status, session_id, now_ms, and reader states. Scan/reject messages carry kind, session_id, an increasing safe integer id, source (barcode or nfc), and monotonic at_ms. A scan adds code as a string. A rejection adds a human-readable message.

The service may emit delivery_error for a delayed queued frame, with its sequence ID and a fresh error timestamp; it never converts that frame into a later movement. consumer_overflow is terminal and carries a message explaining that the label must be rescanned. Queues exist only for bounded live transport while a consumer is connected. Nothing is kept for offline operation or a later browser.

USB frame timestamps originate in Linux evdev. The reader requests CLOCK_MONOTONIC through EVIOCSCLOCKID before taking the exclusive device grab. It retains the first key's timestamp, validates inter-key gaps, and rejects stale/future buffered input and SYN_DROPPED frames. An unsupported clock configuration remains a visible reader error. It never silently re-dates an old kernel buffer as a fresh scan after a paused process resumes.

## Supported Eyoyo keyboard sequence

The physical EY-H2 (`34eb:1502`, reported as `USB HIDKeyboard`) is supported in its observed configuration. No scanner reset, programming barcode, Windows driver, serial-mode change, or browser text box is required. Pin the scanner's actual stable by-id path in `scanner.env`; do not add every generic keyboard to discovery.

Each payload is followed by Enter, Down Arrow, and a Num Lock toggle. Num Lock carries no payload character. Enter completes one frame; Down Arrow is consumed only outside a payload and within 350 ms after that Enter. Down inside a payload or outside this suffix window still rejects. The trailer cannot create a second movement or become the beginning of the next scan. Payload validation, first-character freshness, lost-event rejection, popup gating, and database confirmation still apply.

Eight physical Data Matrix scans matched exactly: short decimal, 39-digit leading-zero value, registered specimen ID, maximum unsigned 128-bit value, mixed case, punctuation, URL, and repeated specimen ID. Every scan had the same Enter/Down/Num-Lock trailer. A sanitized 528-key-event capture is replayed by `test_recorded_eyoyo_hardware_sequence_preserves_all_eight_payloads`. This validates transport for these samples; arbitrary text from the diagnostic sheet is not a valid specimen ID in the kiosk.

The physical Shared-room workflow was also checked against the working database on 2026-10-23: a 12-piece demo stack entered together; numbered OUT moved only 1–3; whole-stack OUT moved only 4–12; Undo restored only 4–12. Final state was nine inside and three finished. Original entry times and the first three exit times were preserved, and the original nine-piece OUT remained in audit history marked undone. USB reconnect, cold-start/outage recovery, and actual printed-label acceptance remain to be verified physically.

Use `SCANNER_DEBUG=true` for exact assembled text, rejections, and delivery messages in the input-service journal. `SCANNER_TRACE_KEYS=true` is optional verbose timing output and should normally be off. Diagnostic programs must release the scanner before starting `fabric-scanner.target`; only one reader should grab the physical device. The separate temporary logger is not part of normal operation.

## Browser acceptance rules

On every stream open the browser samples GET status using performance.now before/after the request. It accepts calibration only within a one-second round trip and computes an offset plus half-round-trip uncertainty. No Date.now/UTC value is used for transport freshness. A failed calibration blocks direct input and retries automatically. New process status invalidates the prior calibration.

The adapter requires the current process session, a new positive safe sequence ID, a supported string code, and a timestamp within the three-second live window. Uncertainty is included conservatively in the age check; future timestamps beyond 100 ms plus calibration uncertainty are rejected. Recent session/sequence state is retained during the page lifetime to prevent reconnect duplicates. Server-side queue-age checks add a second bound.

Before sending a frame to scan(), the page must be visible, database healthy, and free of a pending movement/Undo or selection/early-checkout popup. The stream must be owned/calibrated and at least one reader usable. Unsupported, duplicate, stale, hidden, or blocked frames produce no SQL movement. They are never queued until a later state.

A browser-hidden page does not record streamed scans even if its connection remains open. A visible but OS-unfocused page may record through usable direct input. The shared keyboard listener consumes Enter/Space and accepts a complete frame only if its initial eligibility and room/direction/scope context still match at Enter. It cannot finish a frame begun blocked under a newly changed mode.

## Readiness and fallback

fabricInputBridgeConnected means this page has an open, calibrated stream. fabricInputBridgeReady additionally requires a usable USB or NFC reader. The watchdog heartbeat's input_bridge uses Ready, not merely Connected. A healthy stream with a dead/missing reader cannot bypass focus recovery.

Database health and usable input remain separate internal states. The header shows Online/Offline for the database; unavailable input shows Connect scanner, Scanner in use, or Tap to resume in the main area. Transport details stay in the local status endpoint and logs. The page exposes data-input-ready for diagnostics/tests; the watchdog still uses fabricInputBridgeReady, not the displayed database status. Hosted Mac scanner pages use keyboard capture normally. If the installed Pi service is unreachable, focused/visible keyboard fallback can work once the service's exclusive grab is released. That state does not guarantee a hung hardware service has released its device; use the documented service diagnostics if scans do not arrive. Known competing ownership disables fallback.

The adapter explicitly recreates an EventSource permanently closed by an HTTP 409/502/503. Relying only on native automatic reconnect would strand a station that opened before its local service. Closed retries stop on page disposal. A failed clock request on an otherwise open stream retries calibration without requiring a reload.

## Failure and verification boundaries

Discovery failures retry; ambiguous device matches require a pinned SCANNER_DEVICE instead of grabbing a generic keyboard. The process supervises reader and HTTP threads so unexpected death causes systemd restart. Persistent NFC failures reconnect with GPIO/SPI cleanup. ENABLE_NFC is the installer's canonical setting; legacy NFC is accepted only when the canonical setting is absent.

The browser saves a submitted movement's UUID/payload and reconciles that exact request after a lost response. Undo recovery asks GET /api/transactions/{event_id}, independent of log pagination. These durable browser identities survive Chromium restarts in its dedicated profile; the local transport queue does not.

Python tests cover ownership, overflow/EOF, discovery, kernel buffering/timestamps, frame assembly, the captured Eyoyo sequence, NFC bounds/reconnect, and watchdog/package behavior. Browser tests simulate USB/NFC transport while using the real SQL-backed HTTP API for movements; an additional native EventSource test exercises HTTP unavailable/occupied recovery. Package smoke checks use real compiled assets and mock central API data. Physical input results are recorded above; full movement acceptance, repeated cold-start timing, and printed-label qualification remain separate checks. See [the implementation guide](SCANNER_IMPLEMENTATION_GUIDE.md) and [deployment README](../deploy/scanner-kiosk/README.md).
