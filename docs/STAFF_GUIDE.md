# Staff guide

The staff app is at **http://127.0.0.1:8120** on the Mac, or **http://MAC-ADDRESS:8120** from another computer on the lab network. Pages: Dashboard, New batch, Specimens, Transactions, Backups and System health. API documentation is at `/docs`.

Every physical specimen gets a **permanent sequential number**, linked to a stack. Its label carries only that number plus a check digit, in a 16 × 16 Data Matrix. Each specimen has one ENTRY and one EXIT for its 24-hour conditioning visit.

## New batch: prep sheet to printed labels

One prep sheet makes one **batch**. **New batch** walks through four steps:

1. **Upload the prep sheet** (.xlsx, .xlsm or .csv), or enter stacks by hand. Sheets with sample counts are picked automatically; tick others under **Sheets** to add them.
2. **Check the sheet.** The screen is laid out like the prep sheet. LabWare #, Oracle project, Standard and Regime are entered once at the top of each worksheet. Below them is one row per sheet row, with the sheet's columns in its order: Test, Total Number of Specimens to be Tested, Conditions, Specimens per sample for Conditioning, Sample Number, Sample Type.
   - A blank Test continues the test above it, as on paper.
   - "5 Warp, 5 Fill" counts as 10 pieces.
   - Each sample number makes one stack. Several sample numbers in one row, separated by commas, make one stack each. When the Comments column names the samples ("Sample A-C"), they are filled in for you.
   - Highlighted cells still need a value; **Go to the first one** jumps there.
   - **Source** shows the workbook cells a row came from, plus any import notes. The importer never invents counts or sample numbers; see [EXCEL_IMPORT.md](EXCEL_IMPORT.md).

   **Register** gives every piece its permanent number.
3. **Print labels.** Preview, pick the Mac's printer, print. No printer? **Open PDF**, print it elsewhere, then **I printed these from the PDF** so the dashboard knows. **Print later** leaves the stacks on the dashboard under *Needs labels*. Reprints keep the same numbers. See [PRINTER_AND_LABELS.md](PRINTER_AND_LABELS.md).
4. **Track.** Where the batch's pieces are: labeled → in a room → ready → finished. A batch closes when every piece is scanned out.

A stack holds 1–500 specimens (20 is typical).

## Editing a stack after printing

Open the stack (Dashboard or Specimens) and choose **Edit stack**. You can:

- fix its project, material, test or treatment,
- add specimens, or
- remove specimens.

Existing specimen numbers and printed labels stay valid. Added specimens take the next numbers, even past removed ones, and the label dialog opens with only their new labels selected. A removed specimen keeps its history; scanning its label says it was removed. Every edit is in the transaction history. Project numbers are kept as text, so letters and leading zeroes survive. Treatment wording (AR, 5L, UV, CH…) is copied from the prep sheet and describes separate groups of pieces, not repeated cycles for the same piece.

## Other staff pages

- **Dashboard** (the start page): **Needs attention** (pieces without labels, and labeled pieces never scanned IN), **Batches in progress**, and **Ready to remove** per room, oldest first. Filter by room, project, test, treatment, batch or text; the filters are in the URL, so a filtered view can be bookmarked. Old `#overview` and `#prep` links still work.
- **Specimens → Registered specimens:** each specimen's ENTRY/EXIT and history; reprint selected labels; download session CSV, audit CSV, or a printable conditioning report.
- **Transactions:** change a scan's specimens, time, room or notes; record a missed scan; undo a transaction. Links from a stack or batch show only its activity. No supervisor login or reason is required. The original is always kept in the audit history.
- **Backups:** make or download a backup, set the daily backup and an extra copy folder, restore. See [BACKUPS.md](BACKUPS.md).
- **System health:** the Mac (disk, memory, power, sleep), each Pi, the printer and backups, with a list of problems. The sidebar badge counts warnings. See [SYSTEM_HEALTH.md](SYSTEM_HEALTH.md).

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
