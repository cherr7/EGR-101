# Testing Plan Results

Results against the five criteria in the Testing Plan memo (28 Oct 2026). Raw data is in [testing-data/](testing-data/); the scripts that produced it are in `scripts/`.

| Test | Target | Result | Status |
|---|---|---|---|
| 1. Scan reliability | Mean < 2.0 s per scan; 100% of records match the prep sheet | Mean **1.30 s** over 119 recorded scans (6 runs); **119/119** records match | **Pass** |
| 2. Confirmation speed | p95 and max ≤ 1.0 s, scan to database commit | p95 **14–20 ms**, max **≤ 514 ms** from day 1 to a 5-year database | **Pass** (fewer than 1,000 scans per size; see below) |
| 3. Auditability | 100% field accuracy; no lost history; 5-year database < 10 GB | **0 mismatches** in 383,476 fields (3 seeds); 0 history rows changed; **5.6 GB** at 5 years | **Pass** |
| 4. Ease of use | ≤ 1 manual entry per 100 scans (UDS ≥ 4) | **1** manual entry in 120 scans (a stack skipped while handling) → **UDS 4** | **Pass** |
| 5. Outage resilience | Mean recovery < 60 s after power + network reset; no keyboard/mouse | Not yet run | **To do** |

## Test 1: Scan reliability, and Test 4: Ease of use (same runs)

29 October 2026, Shared room station (Raspberry Pi 5, Eyoyo scanner), Stack mode, IN. Twenty Test 1 stacks of 1–20 pieces (201 pieces; AR/5L/50L; four test codes), one Data Matrix label per stack. Time per scan is the gap between consecutive database commits (`scripts/test1-scan-reliability.py report`); the database was restored to the same baseline before each run.

| Run | Tester | Recorded | Mean s/scan | Slowest gap | Gaps > 5 s | Match prep sheet | Repeat reads | Manual entries |
|---|---|---|---|---|---|---|---|---|
| 1 | A | 20/20 | 1.19 | 1.86 s | 0 | Yes | 0 | 0 |
| 2 | A | 20/20 | 1.08 | 1.84 s | 0 | Yes | 0 | 0 |
| 3 | A | 20/20 | 1.84 | 4.98 s | 0 | Yes | 6 | 0 |
| 4 | A | 20/20 | 1.03 | 2.07 s | 0 | Yes | 0 | 0 |
| 5 | A | 20/20 | 1.12 | 1.96 s | 0 | Yes | 4 | 0 |
| 6 | B | 19/20 | 1.54 | 4.29 s | 0 | Yes | 1 | 1 |
| **All** | | **119/120** | **1.30** | 4.98 s | **0** | **100%** | 11 | **1 (UDS 4)** |

- **Run 6:** stack #1 (label 13, one piece) was never presented to the scanner; the tester missed it while moving stacks. It is counted as one manual entry, since in use it would have to be caught and entered by hand. It is a handling slip, not a read failure.
- **Repeat reads** are re-scans of a stack that had already gone green (a label left in the scanner's view, or held under it). Each returned "already recorded", kept the original time and changed nothing, so none needed a manual entry.
- **Voided attempts:** two earlier attempts stopped partway (4 and 6 stacks) because the scanner was plugged into the Mac rather than the Pi, so scans typed into the wrong window. They are setup errors and are kept in `run-3-void.json` and `run-4-void.json`.

## Test 2: Confirmation speed

The app's own registration and movement code on a simulated clock (`scripts/scale-simulation.py 30 --extend-to 1825`), timing scan input to committed database response with `time.perf_counter()`. Checkins and checkouts alternate through the day. The 5-year point copies 30 simulated days forward, then times one more real day against the full database. Volume assumption: 1,000 specimens per room per day, 3 rooms.

| Database size | Scans timed | p50 | p95 | Max |
|---|---|---|---|---|
| Day 1 (3,000 specimens) | 377 | 5.9 ms | 14.8 ms | 31 ms |
| Day 7 (21,000) | 747 | 7.3 ms | 20.2 ms | 60 ms |
| Day 14 (42,000) | 727 | 6.3 ms | 14.2 ms | 30 ms |
| Day 30 (90,000) | 698 | 6.5 ms | 14.0 ms | 31 ms |
| 5 years (5.49 million specimens, 2.0 million history rows) | 724 | 7.3 ms | 17.5 ms | 514 ms |

Each size has one simulated day of traffic (377–747 scans), short of the memo's 1,000 per size; timing two days per size closes the gap. The simulation bypasses the scanner, Wi-Fi and screen; Test 1's 1.30 s per scan includes them.

**Scaling fix found by this test.** The first 90-day run showed the kiosk's activity list taking 91 s and its inventory 26 s by day 30, because both queries scanned the whole history. After indexing (`room_id` column, expression and GIN indexes, inventory from open sessions):

| Day | Activity list (before → after) | Inventory (before → after) | Staff transactions (before → after) |
|---|---|---|---|
| 1 | 0.13 → 0.08 s | 0.9 → 0.12 s | 0.2 → 0.18 s |
| 30 | 91 → 0.09 s | 26 → 0.14 s | 0.9 → 0.21 s |
| 90 | > 20 → 0.10 s | > 20 → 0.22 s | 3.2 → 0.26 s |

## Test 3: Auditability

**Part A, operation simulation** (`scripts/test3-audit-simulation.py SEED`): 90 days, 40 stacks a day, all writes through the same HTTP API as the staff web interface and kiosks. The script keeps its own ground-truth log, then compares every stack field and every specimen's room, entry, exit and state; it hashes each history row when written and again at the end, and tries to UPDATE and DELETE the history directly.

| | Seed 1 | Seed 2 | Seed 3 | Total |
|---|---|---|---|---|
| Stacks / specimens | 3,600 / 27,060 | 3,600 / 27,881 | 3,600 / 27,428 | 10,800 / 82,369 |
| Scan IN / OUT | 3,944 / 4,080 | 3,944 / 4,094 | 3,924 / 4,115 | 11,812 / 12,289 |
| Partial checkouts | 905 | 897 | 817 | 2,619 |
| Undos | 1,042 | 1,073 | 1,070 | 3,185 |
| Edits (corrected times) | 1,090 | 1,067 | 1,138 | 3,295 |
| Missed scans entered later | 178 | 195 | 191 | 564 |
| Fields checked | 126,240 | 129,524 | 127,712 | 383,476 |
| **Mismatches** | **0** | **0** | **0** | **0** |
| History rows (expected = actual) | 14,839 | 14,870 | 14,855 | — |
| Rows changed after writing | 0 | 0 | 0 | 0 |
| UPDATE / DELETE on history | Refused | Refused | Refused | — |

Missed-scan entries number in the hundreds rather than thousands.

**Part B, five-year storage:** 5,627 MB (5.6 GB) for 5.49 million specimens and 2.0 million history rows, under the 10 GB target. The oldest record is still found in 5 ms. About 1.1 GB a year at 1,000 specimens per room per day.

## Test 5: Outage resilience

Not yet run. Procedure and the reset log sheet are on the last page of `output/test-sheets/run-sheets.pdf` (`scripts/test-run-sheets.py`).
