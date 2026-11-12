# Printing workflow review

Reviewed on November 12, 2026, against the Brother DK-1209 die-cut labels (62 × 29 mm). The walk
followed the whole flow: register → preview → choose printer → print → reprint. It was done in the
staff app in Chromium against a test API, plus the code paths in `frontend/src/LabelDialog.tsx`
and `backend/app/printing.py`.

**Physical test print: not done.** This Mac has no Brother QL print queue. CUPS lists only two HP
OfficeJets, an Officejet 8600 and a Rollo. The label PDF and the new preview image were checked by
decoding every Data Matrix in software (`test_image_preview_shows_the_same_codes_as_the_printed_pdf`,
`test_selective_preview_print_and_audit_use_identical_registered_ids`). The physical check is still
to do. Run it on the Mac the QL-810W is installed on:

1. Register one 1-specimen stack and open **Preview & print**. The printer list should mark the
   Brother queue as a label printer and show no warnings.
2. Print 1 label. The dialog should go from "N label(s) still waiting in the Mac's queue" to "All
   labels have left the Mac's queue" within a few seconds.
3. Scan the label at a kiosk in IN mode. The kiosk must show the same stack and "Piece 01". Then
   tap Undo.
4. Measure it: 62 × 29 mm, with no clipped text and the code at least 3 mm from every edge.

## Where it could go wrong, and what now guards it

| # | What goes wrong | Before | Now |
| --- | --- | --- | --- |
| 1 | **Preview is a blank box.** The dialog embedded the PDF in an iframe, which needs the browser's PDF viewer. It is missing or disabled in kiosk/embedded Chromium and some Safari settings, so it looked "janky" or broken. | Blank grey box with no message. | The preview is a PNG drawn with the same layout code as the PDF (`exports.labels_png`). Every browser shows it. **Open PDF** still opens the exact print file. |
| 2 | **Preview flashes and lags.** Each checkbox click cleared the preview and rebuilt a PDF of every selected label (228 pages for the fake sheet). | Blank flash on every click. | The old image stays until the new one is ready. The image draws only the first 12 labels. |
| 3 | **Wrong printer.** With no queue named like the QL, the dialog chose the Mac's *default* printer: here an HP OfficeJet, so labels would print on Letter paper. | Silent default. | No default choice. Non-Brother queues are marked "(not a label printer)", with a warning. |
| 4 | **Printer offline.** CUPS on macOS accepts jobs for an offline printer and holds them. The app said "Sent to printer"; staff reprinted; when the printer came back, everything printed twice. | Not visible. This Mac had an HP job stuck since Oct 22. | Each queue shows its CUPS state and alerts (offline, out of labels, jam, cover open, paused). It shows "N job(s) are already waiting" before you print. After printing, the dialog checks every 3 s and says when the labels have actually left the Mac's queue, or tells you not to reprint. |
| 5 | **Lost response / double click.** | One request ID per print, saved before sending. Retries reuse it, and a "submitting" claim blocks a second `lp`. | Unchanged, now with sequential IDs (`print-<n>`) reserved from the server. Two browsers can no longer pick the same ID. |
| 6 | **Response lost after CUPS accepted the job.** | Marked `unknown` after 60 s. A reprint needs "I checked the queue". | Unchanged. The waiting-jobs count now makes "check the queue" something the dialog shows. |
| 7 | **Wrong roll loaded** (e.g. DK-2205 continuous, or DK-1201 29 × 90 mm). | Nothing. | CUPS can't report which roll is loaded. The QL stops with an error light and the job sits in the queue; that now shows as "still waiting" (row 4) instead of "Sent". The dialog says which roll to load. **Still open:** a 62 mm roll of a different length prints the wrong size without error. Only the physical check catches that. |
| 8 | **Label text goes stale after editing a stack.** Labels printed "Piece 3 / 10"; adding or removing specimens made that total wrong. | Count printed. | Labels print "Stack 12 · Piece 03" with no total. Editing a stack never changes a printed label. |
| 9 | **Reprint prints different labels.** | Same IDs: reprints encode the specimen id. | Unchanged. Adding specimens opens the dialog with only the new labels selected. |
| 10 | **Many labels in one job.** | One CUPS job per label, so the QL doesn't feed blank tape between labels. | Unchanged. `cups_job_id` is now `TEXT`, so a large job's comma-separated IDs can't overflow the old 200-character column. |

## Left as is

- **Printing from a Pi kiosk.** Not offered; labels are printed at the Mac only.
- **Auto-cancel of stale CUPS jobs.** Deliberately not automatic: cancelling labels someone is
  waiting for is worse than a warning. Cancel in *System Settings → Printers & Scanners → Open
  Print Queue*.
- **Page size name.** Jobs ask for `media=Custom.62x29mm`. If the installed Brother driver needs
  its own name for DK-1209, set it after the physical test and add an assertion to
  `install_fake_cups`.
