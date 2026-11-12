# Oral Presentation #2 — Slide Outline (Team FFF)

**Project:** Automated, auditable check-in/check-out of fabric specimen stacks at UL Solutions' conditioning rooms
**Team:** William Zhang · Max Rerisi · Raquibul Hasan · Dereknick Garcia-Morales
**Client:** UL Solutions (Michael Seward, Travis Westphal)
**Format (WB p. 65–66):** 7–8 min talk + 1–2 min Q&A · target 10–12 slides · PowerPoint only · embedded videos set to autoplay · every member speaks about equally

> This is an outline only, not a deck. Each slide lists: **title** (written as a takeaway, per the Visuals rubric), **on-slide content** (keep it short), **visual**, **talking points**, and the **rubric line** it covers.
> 🔧 **HARDWARE PLACEHOLDER** blocks are for the hardware team to fill in. Anything already documented in the memos or repo is pre-filled under "Known so far". Check it and replace the `[TO FILL]` items.

---

## Time budget (8:00 max)

| Section | Slides | Time | Rubric weight |
|---|---|---|---|
| Intro, motivation, problem, design preview | 1–3 | 1:00 | 10 |
| **Software §1:** Brainstorm and decision making (+ HW) | 4–6 | 1:45 | 15 |
| **Software §2:** Prototype evolution (+ HW) | 7–9 | 1:45 | 10 |
| **Software §3:** Final design, demo, testing (+ HW) | 10–13 | 2:30 | 10 |
| Future plans and conclusion | 14–15 | 1:00 | 10 |
| References and backups (not presented) | 16+ | — | — |

That's 15 presented slides, which is over the 10–12 target. If you need to cut, merge 6 into 5, 9 into 8, and 13 into 12. The hardware placeholders then become the right half of those shared slides.

---

## Asset index (everything here is in `assets/`)

| Figure | File | Used on |
|---|---|---|
| System architecture | [fig-08-architecture.png](assets/figures/fig-08-architecture.png) | 3 |
| Decision process funnel | [fig-01-decision-process.png](assets/figures/fig-01-decision-process.png) | 4 |
| Weighted Pugh matrix (A & E highlighted) | [fig-02-weighted-pugh.png](assets/figures/fig-02-weighted-pugh.png) | 5 |
| Original vs. built decisions | [fig-03-decision-evolution.png](assets/figures/fig-03-decision-evolution.png) | 6 |
| UI evolution, low → medium → final fidelity | [fig-04-ui-evolution.png](assets/figures/fig-04-ui-evolution.png) | 7 |
| Database evolution v0 → v3 | [fig-05-database-evolution.png](assets/figures/fig-05-database-evolution.png) | 8 |
| Label/ID evolution (real decoded codes) | [fig-07-label-evolution.png](assets/figures/fig-07-label-evolution.png) | 8 |
| Final database schema | [fig-06-final-schema.png](assets/figures/fig-06-final-schema.png) | 11 |
| **UI flowchart (final design)** | [fig-09-ui-flowchart.png](assets/figures/fig-09-ui-flowchart.png) | 10 |
| Test 1 scan time per run | [fig-10-test1-scan-time.png](assets/figures/fig-10-test1-scan-time.png) | 12 |
| Test 2 latency vs. database size | [fig-11-test2-latency.png](assets/figures/fig-11-test2-latency.png) | 12 / backup |
| Indexing fix found by testing | [fig-12-indexing-fix.png](assets/figures/fig-12-indexing-fix.png) | 12 |
| Test scorecard, all 5 tests | [fig-13-test-scorecard.png](assets/figures/fig-13-test-scorecard.png) | 12 |
| Final UI screenshots (12) + printed label | `assets/screens/final/` | 10, backups |
| Prototype-round screenshots | `assets/screens/prototypes/` | 7 |
| Raw test data (copied from `origin/main`) | `assets/data/` | 12 |

For section 3, I chose a **UI flowchart** over a video: it reads well on a projector, it can't fail to autoplay, and every screen in it is a real capture from the final build. If you also want to demo live, use the backup plan on slide 10.

---

# Intro

### Slide 1 — Title: "Scan once, audit forever: automated sample tracking for UL's conditioning rooms"
- **On slide:** Title, team names and photos, UL Solutions logo (only if UL approves), EGR 101 / date.
- **Talking points:** Each member introduces themself and their subsystem (physical/hardware, database front end, database back end, label printing).
- **Rubric:** Introduced full team.

### Slide 2 — Hook + problem: "A missing timestamp can force UL to redo a whole test"
- **On slide (few words):**
  - Firefighter PPE is tested at UL. Every sample needs **≥ 24 h** at a controlled temperature and humidity first.
  - Today: technicians type IN/OUT times into a separate Word datasheet for each project. They leave the room to do it.
  - Missed or wrong entry → no proof of 24 h → redo conditioning → testing delayed.
  - Problem statement (one line): *UL lab staff need an automated, auditable way to track groups of samples entering and leaving conditioning rooms.*
- **Visual:** Reuse the OP1 hook photo (firefighters) and the OP1 "Sample received → 24 h → manual log → audit" flow, at a smaller size.
- **Talking points:** Three rooms. PPE 23 ± 3 °C / 65 ± 5 % RH. Shared 23 ± 1 °C / 65 ± 2 % RH. Footwear 21 ± 3 °C / 50 ± 5 % RH. A stack holds up to ~20 specimens from one project / test / laundering code (AR, 1L–100L). Top criteria from the pairwise chart: auditable database > scan accuracy > outage resilience > multi-item tracking > speed. Constraints: < $5,000 initial, ≤ $1 per stack.
- **Rubric:** Need for design + problem statement.

### Slide 3 — Design preview: "One scan at the door records every specimen in the stack"
- **On slide:** Print a Data Matrix label per specimen → scan at a Pi touchscreen station → the server commits to an append-only PostgreSQL history → green confirmation.
- **Visual:** [fig-08-architecture.png](assets/figures/fig-08-architecture.png). Dashed gray boxes = hardware.
- **Talking points:** Give the whole solution early (rubric). Three door stations (Raspberry Pi 5 + Touch Display 2 + USB 2D scanner) talk over the LAN to one Mac host. That host runs the web server, the staff app and the database. Staff use a browser to import prep sheets, print labels, and review or correct records.
- **🔧 HARDWARE PLACEHOLDER — integrated design preview**
  - Known so far: Pi 5 + 1280×720 Touch Display 2 per door. Eyoyo EYH2 USB scanner (keyboard-wedge, sends digits + Enter). Brother QL-810W direct-thermal printer on a 62 mm roll.
  - `[TO FILL]` Photo of the assembled station at a door, labeled (screen, scanner, mount, power), with dimensions.
  - `[TO FILL]` Mounting location and height. How the stack is presented to the scanner (handheld vs. stand).
- **Rubric:** Presented overview of design early.

---

# Software Section 1 — Brainstormed solutions and decision making (15 pts)

### Slide 4 — "17 concepts → 5 integrated designs → 2 advanced"
- **On slide:** The funnel figure only, plus a two-line caption.
- **Visual:** [fig-01-decision-process.png](assets/figures/fig-01-decision-process.png)
- **Talking points (brainstorm and screening):**
  - We split the system into three blocks: **identification medium** (6 ideas), **scanning architecture** (6), **database** (5).
  - Each block got its own Pugh screening matrix (+ / 0 / −) against lab baselines: 1D barcode, handheld scanner gun, spreadsheet.
    - ID medium: OCR −3, 1D barcode 0, **2D barcode +1**, NFC 0, **passive UHF RFID +2**, BLE beacon +1. Advanced: barcode (1D/2D) + RFID.
    - Scanning: **handheld 0**, **fixed +2**, **walk-through −1**, conveyor −1, smartphone −1. Advanced: handheld, fixed, walk-through.
    - Database: Excel/SharePoint 0, enterprise platform −2, central cloud SQL 0, **local SQLite + cloud sync +3**. This was the one "universal" pick for every design.
  - Morphological chart → Solutions A–E (all share the SQL database).
  - Contrast the top two briefly. **E = RFID fixed pad:** no line of sight, fast, durable. **A = barcode handheld:** cheap, easy to build.
- **Backup slide:** The three screening matrices as full tables (Design Evaluation Memo, Tables 2.1–2.3).
- **🔧 HARDWARE PLACEHOLDER — hardware brainstorm details**
  - Known so far (from the memo): Hardware options were handheld scanner (moves to the pile), fixed/stationary scanner (pile brought to it), walk-through doorway portal, automated conveyor, smartphone camera app. Fixed scored best on ease of use and durability. Walk-through and conveyor lost on installation feasibility, cost and false triggers. Smartphone lost on workflow speed, durability and false triggers.
  - `[TO FILL]` Sketches or photos of hardware concepts (portal antenna, fixed pad, handheld) if the team has them.
- **Rubric:** Summarized decision process; described key brainstormed solutions.

### Slide 5 — "Pugh scoring: RFID fixed pad (E) won, but barcode (A) was advanced too"
- **On slide:** The matrix. Highlight columns A and E and the two weights that decided it (accuracy 40 %, speed 30 %).
- **Visual:** [fig-02-weighted-pugh.png](assets/figures/fig-02-weighted-pugh.png) (totals recomputed from the ratings and checked against the memo: A 2.85, B 2.65, C 4.10, D 3.60, E 4.65)
- **Talking points:**
  - E won (4.65) on first-scan accuracy (5) and speed (5). No line of sight is needed, so a 20-piece stack registers in one pass.
  - A placed 4th (2.85) but scored 5/5 on cost (about $0.01 of paper per stack) and is fastest to build and debug.
  - Decision: **advance A and E in parallel.** More than 90 % of the software is shared (database, audit rules, UI). Only the input differs: keystrokes from a barcode scanner vs. serial packets from an RFID reader.
- **Rubric:** Final decision matrix; contrasted top candidates; justified the selection.

### Slide 6 — "What we decided in September vs. what we built"
- **On slide:** The evolution table (six rows). Bold the three biggest changes: label meaning, database, corrections.
- **Visual:** [fig-03-decision-evolution.png](assets/figures/fig-03-decision-evolution.png)
- **Talking points (original decision → how it evolved after development):**
  1. **ID medium:** Planned RFID (E) + barcode (A) in parallel → built **barcode (2D Data Matrix) first, as planned**. It met the Test 1 targets. RFID stays a future upgrade. An optional NFC tag reader exists in the Pi package as an add-on.
  2. **Label meaning:** "one label on top = whole stack" → **one permanent number per specimen, linked to its stack.** Scanning any piece finds the stack. This was needed because staff pull *part* of a stack, and the audit needs per-specimen ENTRY/EXIT.
  3. **Database:** local SQLite cache + cloud sync with a 24 h offline queue → **one PostgreSQL server on a Mac host.** If it's unreachable, stations show **OFFLINE** and refuse scans. This came from the Project Outline clarification ("it should just say NOT AVAILABLE"). It avoids sync conflicts in the audit trail.
  4. **Outage criterion:** "≥ 24 h offline storage" → replaced in the Testing Plan memo with **"recover to scan-ready in < 60 s after power + network loss"** (Test 5).
  5. **Scan workflow:** scan → review → Submit (Round 3) → **scan records automatically.** This meets the "one scan, zero typed entries" criterion.
  6. **Corrections:** supervisor login + mandatory reason (Round 1 concepts) → **anyone can edit, and the database keeps every original** (append-only history; the DB refuses UPDATE/DELETE).
- **🔧 HARDWARE PLACEHOLDER — how hardware decisions evolved**
  - Known so far (repo): RFID (Solution E) was not built. The scanner is the Eyoyo EYH2 2D imager. The prototype display went from 800×480 (Round 2 mockups) to a 1280×720 Touch Display 2. The old Dymo text-only printer was replaced by a Brother QL-810W.
  - `[TO FILL]` Why RFID was deferred from the hardware side (cost quote? tag durability? reader availability?). Scanner and mount choice rationale. BOM with total cost vs. the < $5,000 constraint.
- **Rubric:** Sufficient details to justify solution.

---

# Software Section 2 — Prototype evolution: UI and database (10 pts)

### Slide 7 — "Three UI rounds before the real build"
- **On slide:** Four columns, one per round, each with a room scanner + staff correction screenshot and one "kept / changed" line.
- **Visual:** [fig-04-ui-evolution.png](assets/figures/fig-04-ui-evolution.png). Single shots are in `assets/screens/prototypes/` if you'd rather build the slide yourself.
- **Talking points (low → medium fidelity, with what testing taught us):**
  - **Round 1, low fidelity (Oct 7):** 40 clickable concepts: 16 scanner workflows, 16 correction workflows, 8 arrangements of lab tools. All simulated, no real database. *Verified:* every primary flow plus 132 layout checks at 3 widths. *Kept:* persistent IN/OUT, whole-stack by default. *Dropped:* supervisor sign-off + reason for edits.
  - **Round 2, medium fidelity (Oct 8 feedback):** 8 scanner layouts at the 800×480 screen size and 6 transaction-log editors. A shared stack QR code. Whole-stack OUT moves only the pieces still inside (after 3 of 10 leave, the next OUT moves the other 7). Repeat scans do nothing. Under-24 h OUT opens an override popup. *Verified:* 64 movement checks + 56 layout checks, 0 JS errors.
  - **Round 3, medium fidelity:** one ID per specimen (Data Matrix), one ENTRY + one EXIT each, whole-transaction Undo, popup editor, stack overview. Scanner input captured across the whole page (no text box to focus). *Changed after:* removed the review/Submit tap, and moved from a mock API to the real database.
  - **Final (Oct 22 → Nov 2):** real FastAPI + PostgreSQL + React. The Pi runs the kiosk at 1280×720. A scan auto-records. Green appears only after the database commit.
- **Rubric:** 2+ low/medium-fidelity designs; strengths, limitations and advancement rationale.

### Slide 8 — "The data model went from a scan log to database-enforced rules"
- **On slide:** The database evolution figure, with the label-evolution strip underneath (or on a split slide).
- **Visuals:** [fig-05-database-evolution.png](assets/figures/fig-05-database-evolution.png) + [fig-07-label-evolution.png](assets/figures/fig-07-label-evolution.png). All three codes were decoded to confirm: Round 2 QR → a shared stack ID; Round 3 → 40 binary digits; final → "210".
- **Talking points (technical basis for each change):**
  - **v0, memo:** a stack-level label holding project / status / test. Local SQLite + cloud SQL. Raw scan log + separate audit record. 24 h offline queue.
  - **v1, Round 2:** JSON transaction log. Edits append a `modify_movement` event that points to the original, so history is never overwritten. This idea survived to the end.
  - **v2, Round 3:** 40-bit integer ID per specimen. Each specimen has one ENTRY, one EXIT, and a `raw_json_time` history. Undo reverses every specimen in a transaction. Still a mock API.
  - **v3, final PostgreSQL:**
    - A unique index allows only **one conditioning period per specimen**. A CHECK requires exit ≥ entry.
    - A **trigger refuses UPDATE/DELETE on `audit_events`** (append-only).
    - A retry ID (station name + counter, e.g. shared-door-118) makes retries safe after a lost response.
    - A version number blocks stale edits.
  - **Label encoding:** Round 3's scanner sent the ID as 40 binary digits. The final label is a sequential number + a Damm check digit in a fixed 16×16 Data Matrix. Digits only, so keyboard layouts can't garble the input, and the check digit catches any single-digit typo or neighbor swap.
- **Rubric:** Technical basis for design decisions.

### Slide 9 — 🔧 HARDWARE PLACEHOLDER — "Hardware prototype evolution"
- **Suggested structure:** 2+ low/medium-fidelity hardware prototypes. For each: photo with labels and dimensions, what it tested, what failed, what changed.
- **Known so far (from the memos and repo, to verify):**
  - Display: Round 2 UI mocked at **800×480** → final **Raspberry Pi 5 + Touch Display 2, 1280×720 landscape**.
  - Scanner: **Eyoyo EYH2** USB 2D imager used as a keyboard wedge. An early scanner trial showed it sent binary-text payloads. The team then switched to **digits + Enter** so USB keyboard layouts can't alter characters. Physical checks so far: 8 exact Data Matrix payloads decoded, plus a 12-piece IN → partial OUT → remaining OUT → Undo run on the real scanner.
  - Label/printer: the old **Dymo** (text-only) was replaced by a **Brother QL-810W** (direct thermal, 62 mm continuous roll, cuts after each label). Label size: 54 × 25 mm letter-sheet demo (Round 2) → 2 × 1 in → briefly 62 × 31 mm → final **62 × 25.4 mm** with 3 mm margins (the Test 1 label PDF is 62 × 25.4 mm). The symbol went from 20×20 to a fixed **16×16 Data Matrix at 19.4 mm**.
  - Plug-and-play boot: Pi autologin → Chromium kiosk → watchdog. A local "Starting station…" page appears before the network is up (Project Outline requirement: after a power loss it must go straight back into the app).
  - Optional PN532 NFC reader supported by the Pi input service. It was an alternative to the barcode, not used in testing.
  - Lesson from Test 1: two attempts were voided because the scanner was plugged into the Mac instead of the Pi. → `[TO FILL]` Did this lead to labeling ports, a fixed cable, or a mount?
- `[TO FILL]` Photos of the station mock-ups and enclosure/mount iterations, with dimensions. Materials, fasteners and power supply choices. What each prototype taught you.
- **Rubric:** Prototype evolution (hardware half).

---

# Software Section 3 — Final design, demonstration, and testing (10 pts)

### Slide 10 — "Final design: from prep sheet to audited 24 h in eight screens"
- **On slide:** The flowchart. Keep the step titles; no extra text.
- **Visual (chosen format: UI flowchart):** [fig-09-ui-flowchart.png](assets/figures/fig-09-ui-flowchart.png). Captured 3 Nov 2026 from the final build (`origin/main`) on an isolated test database with synthetic project numbers.
- **Talking points (walk the arrows):**
  1. **Import prep sheet:** Excel/CSV upload, review rows, register. Every specimen gets a permanent number.
  2. **Print labels:** one 62 × 25.4 mm Data Matrix label per specimen. Reprints keep the same IDs.
  3. **Scan IN at the door:** room and IN stay selected; there's no text box and no Submit button.
  4. **Green = committed:** green appears only after the database commit, then stays 12 s without blocking the next scan.
  5. **Track the 24 h:** the staff "Stacks" view sorts ready stacks first and shows exact ready / inside / finished counts.
  6. **Scan OUT, part of a stack:** "Individual" mode opens numbered checkboxes. A later whole-stack OUT moves only what's left.
  7. **Early OUT is flagged:** under 24 h, the override is recorded as incomplete conditioning.
  8. **Fix mistakes, keep history:** edit time, room or selected pieces, or undo a transaction. The original observation is kept.
  - **Failure path:** database unreachable → red **Offline**, IN/OUT disabled ([08-scanner-offline.png](assets/screens/final/08-scanner-offline.png)).
- **Optional live demo (≈ 30 s):** scan two labels at a Pi station with the room inventory open. Backup plan if the demo fails: stay on this slide.
- **Extra single screenshots** for builds or backups: `assets/screens/final/01…12-*.png` (scanner shots at 1280×720 ×2, staff shots at 1440×900 ×2).
- **Rubric:** Presented current design clearly; demonstrated how the prototype works.

### Slide 11 — "Technical details: every scan is one all-or-nothing database transaction"
- **On slide:** Schema figure on the left. Three bullets on the right: atomic movement, append-only audit, safe retry.
- **Visual:** [fig-06-final-schema.png](assets/figures/fig-06-final-schema.png)
- **Talking points:**
  - **Stack:** FastAPI (Python), PostgreSQL 18, React/TypeScript. The Pi keeps its own compiled UI, so it can show "Offline" even when the Mac is down.
  - **A movement request:** locks the stack, writes conditioning sessions plus one audit event, and commits all or none. Stale selections and checkouts from the wrong room are rejected.
  - **Displayed ENTRY/EXIT** is derived from sessions. Corrections and Undo are new events.
  - **Automated software checks (Oct 23):** 62 PostgreSQL integration tests, 44 browser checks, 8 Mac packaging checks, 59 Pi package / NFC / watchdog checks, all passing. On the dev laptop, a 20-specimen scan took 118 ms from scanner Enter to green.
- **🔧 HARDWARE PLACEHOLDER — technical details of the station**
  - `[TO FILL]` Labeled drawing of the station with length scales. Materials, fasteners and mounting. Power supply (Pi 5 PSU spec). Cable routing. Scanner stand/holder. Enclosure rated for the room (23 ± 1 °C, 65 ± 5 % RH).
  - `[TO FILL]` Printer setup: macOS print queue, roll type, label adhesion on fabric.
- **Rubric:** Technical details and technical basis for the selected solution.

### Slide 12 — "Testing: 4 of 5 criteria met; one test caught a real scaling bug"
- **On slide:** Scorecard. The Test 1 chart can be a second build or a small inset. Mention the indexing fix in one line.
- **Visuals:** [fig-13-test-scorecard.png](assets/figures/fig-13-test-scorecard.png), [fig-10-test1-scan-time.png](assets/figures/fig-10-test1-scan-time.png), [fig-12-indexing-fix.png](assets/figures/fig-12-indexing-fix.png). Test 2 detail: [fig-11-test2-latency.png](assets/figures/fig-11-test2-latency.png).
- **Results (real data: `docs/TEST_RESULTS.md` + `docs/testing-data/` on `origin/main`; I recomputed the Test 1 mean and the Test 3 totals from the raw files and they match):**

  | Test | Target | Result | Status |
  |---|---|---|---|
  | 1 Scan reliability (29 Oct, Pi 5 + Eyoyo, 6 runs × 20 stacks of 1–20 pieces) | mean < 2.0 s/scan; 100 % match prep sheet | **1.30 s** mean; **119/119** records match; slowest gap 4.98 s; 0 gaps > 5 s | Pass |
  | 2 Confirmation speed (app code, simulated clock) | p95 and max ≤ 1.0 s | p95 **14–20 ms**, max **≤ 514 ms**, from day 1 to a 5-year DB (5.49 M specimens) | Pass* |
  | 3 Auditability (90-day sim × 3 seeds + 5-year fill) | 100 % field accuracy; 5-year DB < 10 GB | **0 mismatches in 383,476 fields**; 0 history rows changed; UPDATE/DELETE refused; **5.6 GB** at 5 years | Pass |
  | 4 Ease of use (same runs as Test 1) | UDS ≥ 4 (≤ 1 manual entry / 100 scans) | **1 manual entry in 120 scans** (tester skipped a stack while handling) → **UDS 4** | Pass |
  | 5 Outage resilience | mean recovery < 60 s after power + network reset | **Not yet run** (procedure and run sheet ready) | To do |

  \* Only 377–747 scans were timed per database size vs. the 1,000 planned. Say this if asked.
- **Talking points (testing in the context of refinement, which the WB asks for):**
  - The first 90-day scale run found the kiosk **activity list took 91 s and the inventory 26 s by day 30**, because both queries scanned the whole history. After adding indexes and reading inventory from open sessions: **0.10 s and 0.22 s at day 90**. Testing changed the design.
  - Honest notes: 11 repeat reads happened (labels left in the scanner's view). Each returned "already recorded" and changed nothing. Two attempts were voided because the scanner was plugged into the wrong computer (setup error, data kept in the repo).
- **Rubric:** Strengths and limitations through testing; technical basis.

### Slide 13 — 🔧 HARDWARE PLACEHOLDER — "Final hardware design and hardware testing"
- **Known so far:**
  - Test 1 and Test 4 already ran on the real hardware: a Shared-room Raspberry Pi 5 station with the Eyoyo scanner, 201 pieces across 20 stacks, one 62 × 25.4 mm Data Matrix label per stack (`docs/testing-data/test1-labels.pdf`).
  - **Test 5 (outage resilience) is the hardware test still to run.** Procedure: cut station power and network for 10 s, restore both, time until the scan-ready (server-connected) screen appears, then scan a reference label. Repeat ≥ 10 times. Pass = mean < 60 s, no keyboard or mouse needed, every post-reboot scan recorded. Reference labels: `docs/testing-data/test5-reference-prep-sheet.csv`. Run sheet: last page of `docs/testing-data/run-sheets-test1-4-5.pdf`.
  - Hardware acceptance checks still listed as open in the repo: printer qualification (sizing, cutter, first-read rate), **label adhesion and heat/laundering durability on fabric**, USB reconnect, Pi cold boot with the server down, Mac reboot/startup service.
- `[TO FILL]` Final station photo(s) with labels and dimensions. Test 5 results table (10 resets, mean/max recovery time). Any adhesion or print-readability results. Final BOM and cost vs. < $5,000 and ≤ $1 per stack.
- **Rubric:** Current prototype (hardware half); testing.

---

# Future plans and conclusion (10 pts)

### Slide 14 — "Next steps: prove it in UL's lab"
- **On slide (two columns):**
  - **Software:**
    - Site visit: UL technicians do one Test 1 run, and UL audit staff review an exported audit trail (both planned in the Testing Plan).
    - Time 1,000+ scans per database size to finish Test 2 as planned.
    - Server-side pagination for big inventories. At the 5-year size the kiosk inventory took 7.5 s and staff stack search 1.9 s in the scale simulation.
    - Maintenance screen: station heartbeat, printer queue, backup age.
    - Optional SharePoint/Microsoft 365 upload and Word datasheet completion.
  - **🔧 Hardware `[TO FILL]`:**
    - Run Test 5.
    - Qualify the printer and label stock on real fabric.
    - Finalize the mount/enclosure for all 3 doors.
    - Decide whether to evaluate RFID (Solution E) as an upgrade.
- **Talking points:** How the final device will look and work in the lab (WB asks for this): three door stations, one office host, labels printed at prep.
- **Rubric:** Reasonable plans for further development.

### Slide 15 — Summary: "One scan replaces a typed entry, and the history can't be erased"
- **On slide (3 bullets max):**
  - Replaces manual Word-datasheet logging with one scan per stack: 1.30 s per scan, 119/119 records correct.
  - Built-in auditability: append-only history, 0 errors in 383k checked fields, 5 years fits in 5.6 GB.
  - Next: outage test, hardware qualification, on-site trial with UL.
- **Close:** Thank UL (Michael Seward, Travis Westphal). Invite questions. Repeat each question before answering (Q&A rubric).
- **Rubric:** Summarized implications; planned conclusion.

---

# Slide 16 — References (keep in reserve at the end)
- Team FFF, *Problem Statement and Design Criteria* memo, 8 Sept 2026.
- Team FFF, *Evaluation of Design Decisions* memo, 28 Sept 2026 (screening matrices, morph chart, weighted Pugh).
- Team FFF, *Testing Plan* memo, 28 Oct 2026.
- Team FFF, Oral Presentation #1, Fall 2026.
- Repository `cherr7/EGR-101`, branch `main`: `docs/TEST_RESULTS.md`, `docs/testing-data/`, `docs/PROJECT_STATUS.md`, `docs/BARCODE_AND_PRINTER.md`, `README.md`.
- GS1 DataMatrix Guideline (symbol sizing). Brother QL-800/810W/820NWB Raster Command Reference (printable area).
- `[TO FILL]` Hardware datasheets (Raspberry Pi 5, Touch Display 2, Eyoyo EYH2, Brother QL-810W) and any vendor quotes.

# Backup slides (for Q&A)
- **B1:** The three Pugh screening matrices (memo Tables 2.1–2.3) and the morphological chart (Table 2.4).
- **B2:** Test 2 latency chart, [fig-11-test2-latency.png](assets/figures/fig-11-test2-latency.png).
- **B3:** Test 1 per-run table: means 1.19 / 1.08 / 1.84 / 1.03 / 1.12 / 1.54 s; testers A ×5, B ×1; repeat reads 0/0/6/0/4/1.
- **B4:** Test 3 detail. Per seed: 3,600 stacks, ~27k specimens, ~870 partial checkouts, ~1,060 undos, ~1,100 edits, ~190 missed scans entered later. Totals: 10,800 stacks, 82,369 specimens, 2,619 partial checkouts, 3,185 undos, 3,295 edits, 564 missed scans.
- **B5:** Offline screen [08-scanner-offline.png](assets/screens/final/08-scanner-offline.png) and room inventory [07-scanner-inventory.png](assets/screens/final/07-scanner-inventory.png).
- **B6:** "Why not RFID?" Talking points from slides 5–6, plus 🔧 `[TO FILL]` the hardware team's cost and durability reasoning.

---

## Visual rubric checklist (20 pts)
- [ ] Every title is a takeaway sentence, not a topic word.
- [ ] Body text ≥ 24 pt. Figure text is already sized for a 16:9 slide at full width, so don't shrink figures below about half the slide width.
- [ ] Plain light background behind figures. The OP1 gradient template sits behind diagrams, so use a solid area on figure slides.
- [ ] Hardware photos have labels **and dimensions** (rubric asks for "labels and dimensions").
- [ ] If you add a video, embed it and set it to autoplay (WB requirement).

## Provenance and regeneration
- Figures: `python3 assets/make_figs.py presentation/oral-presentation-2`, run from the repo root (needs matplotlib + Pillow). It reads only `assets/data/` and `assets/screens/`.
- Screenshots: `assets/capture-pres.mjs` (copy it into `scripts/` of a `main` checkout first, since it imports `../frontend/node_modules`), run against an isolated test API on `127.0.0.1:8121` built from `origin/main`. It never touches the lab database.
- Raw test data in `assets/data/` is copied unchanged from `origin/main:docs/testing-data/`.
