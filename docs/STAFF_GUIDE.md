# Staff guide

The staff app is at **http://127.0.0.1:8120** on the Mac, or **http://MAC-ADDRESS:8120** from another computer on the lab network. It has four pages: Stacks, Specimens, Transactions and Backups. API documentation is at `/docs`.

Every physical specimen gets a **permanent sequential number**, linked to a stack. Its label carries only that number plus a check digit, in a 16 × 16 Data Matrix. Each specimen has one ENTRY and one EXIT for its 24-hour conditioning visit.

## From prep sheet to printed labels

1. **Specimens → Prepare / import:** upload the prep sheet (.xlsx or .xlsm). CSV and manual entry also work. The app shows the worksheets it found and any source warnings. Macros are never run.
2. **Review the rows.** Each row is one material/sample, test and exact treatment text. Fill in anything missing (project, material, count). Include every piece going into conditioning, spares included. Split combined groups before registering. The importer never invents counts or material IDs; see [EXCEL_IMPORT.md](EXCEL_IMPORT.md) for how prep-sheet columns are read.
3. **Register.** This creates the stack and its numbered specimens. Registering the same draft twice returns the existing registration. Uploading the same workbook again reuses its draft unless you import it as a new batch.
4. **Preview & print.** Check the labels, pick the Mac's printer queue and press Print. Reprints keep the same IDs. PDF and CSV downloads work when the printer doesn't. See [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md).

A stack holds 1–500 specimens (20 is typical). Extra pieces found later are registered as a new group, not added to an existing stack. Project numbers are kept as text, so letters and leading zeroes survive. Treatment wording (AR, 5L, UV, CH…) is copied from the prep sheet and describes separate groups of pieces, not repeated cycles for the same piece.

## Other staff pages

- **Stacks:** ready stacks first; sort by longest time inside; filter by room, status, project, material, test or treatment. Partly-ready stacks show exact ready/inside/finished counts.
- **Specimens → Registered specimens:** each specimen's ENTRY/EXIT and history; reprint selected labels; download session CSV, audit CSV, or a printable conditioning report.
- **Transactions:** change a scan's specimens, time, room or notes; record a missed scan; undo an eligible transaction. No supervisor login or reason is required. The original is always kept in the audit history.
- **Backups:** make or download a checkpoint, set daily snapshots and a copy folder, validate and restore. See [BACKUPS.md](BACKUPS.md).

## Room scanner (Pi station)

The station shows PPE / Shared / Footwear, Stack / Individual, and green **Online** / red **Offline** across the top, and **IN**, **OUT** and **Undo** at the bottom.

1. Check the right room is highlighted.
2. Tap **IN** or **OUT**. It stays selected until you change it, even after a reload.
3. Scan any label in the stack. A normal whole-stack movement records by itself; there is no Submit.
4. Green means the database confirmed it. It stays 12 seconds but you can scan the next stack straight away.

What happens:

- **Stack IN** moves every member that hasn't entered yet. Scanning any one piece finds its stack.
- **Stack OUT** moves only members still inside. If 3 of 10 already left, the next stack OUT moves the other 7.
- **Individual** opens numbered checkboxes; confirm to move exactly those pieces.
- **OUT before 24 hours** asks for an override, which records the real removal as incomplete conditioning.
- **Undo** reverses the newest scan in Activity and marks it Undone. It never steps back to an older scan.
- **Scanning a stack again** says "already recorded" and keeps the original time.
- **Errors and duplicates** show for four seconds; scan again immediately.
- **Offline** stays on screen until the Mac is reachable again. Nothing is recorded or queued while offline; rescan once it's back.

The expand button (upper right) opens detailed Activity with a minus button per movement that reverses that exact transaction. **Inventory** shows what's in the selected room (Ready, Inside, Next 2h, by project) and refreshes every ten seconds. Scans are ignored while either popup is open, so close it before scanning.

Any browser can open a scanner page at `http://MAC-ADDRESS:8120/kiosk?station=shared-door` (or `ppe-door`, `footwear-door`). It uses keyboard input, so that window needs focus.

## Conditioning rules

| Station | Room | Temperature | Relative humidity | Required time |
| --- | --- | --- | --- | --- |
| shared-door | Shared | 23 °C ±1 °C | 65% ±2% | 24 hours |
| ppe-door | PPE | 23 °C ±3 °C | 65% ±5% | 24 hours |
| footwear-door | Footwear | 21 °C ±3 °C | 50% ±5% | 24 hours |

"Ready" means the recorded continuous time in the room reached 24 hours. Room temperature and humidity are measured by the lab's existing system, not this app. The app doesn't track the later four-hour post-oven step, laundry or other prep stages. Times are stored in UTC and shown in America/New_York.

Each movement locks its stack and commits all or nothing; a retried request returns the original result instead of recording twice. Checkout from the wrong room is rejected. Corrections and undos keep the original records.

## Not part of this app

Word datasheet completion, email or SharePoint intake, staff scheduling and training, test results, environmental monitoring, RFID, and offline scanning. No public internet tunnel or Microsoft 365 integration is installed.
