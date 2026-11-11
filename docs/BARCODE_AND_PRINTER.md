# Specimen IDs, Data Matrix labels, and Brother printing

## Identity and label payload

PostgreSQL numbers every stack and every physical piece sequentially (`bigint` identity columns) when a reviewed prep row is registered. A stack groups its individually numbered pieces. Project numbers and customer/material references remain ordinary prep metadata; they are not generated IDs.

A label carries one thing: the **specimen's label code**, which is its number followed by one [Damm](https://en.wikipedia.org/wiki/Damm_algorithm) check digit. Specimen 12345 is `123459`. The check digit catches every single wrong digit and every swap of two neighbouring digits, so a code typed from a damaged label can never land on another real specimen. Codes are unpadded: 2 digits for the first specimen, 20 for the largest `bigint`. They contain no URL, project data, prefix, treatment, count, or Enter character. Stacks have no labels; scanning any piece's label finds its stack.

The encoder produces ordinary **ECC200 Data Matrix**, always a fixed **16 × 16 module square** plus a one-module quiet zone, regardless of code length. A 16 × 16 symbol holds 24 digits, so every possible code fits one size and every label looks the same. ECC200 has a fixed amount of Reed–Solomon error correction for each symbol size; there is no selectable level as in QR. [GS1 DataMatrix guideline](https://ref.gs1.org/guidelines/datamatrix/)

Digits avoid raw bytes and punctuation in a USB keyboard stream, where layouts and Caps Lock can change other characters. Stack and specimen numbers are JSON integers; at 3,000 specimens a day they stay far below JavaScript's exact-integer limit. Reprinting reuses the same numbers and does not create another physical specimen.

## Scanner transport

The Eyoyo EYH2 is used as a USB keyboard-wedge scanner: decoding a label should type its digits followed by Enter. Enter is a scanner suffix setting, not part of the barcode. Print the acceptance sheet with `python3 scripts/barcode-test-sheet.py`, which writes `output/qa/barcode-test-sheet.pdf`. Page 1 has production-encoder label codes at the 19.4 mm label size, from the shortest (2 digits) to the longest (20 digits). Page 2 has Code 128, Code 39, EAN-13, QR and Data Matrix keyboard-mapping checks. The script decodes every Data Matrix before writing the PDF. Configure and verify the actual device with a simple text editor before relying on the kiosk:

1. Scan an app-generated label into a blank editor.
2. Confirm exactly the digits printed as "No." on the label, then one Enter.
3. Check that no extra prefix/suffix, tab, character substitution, or conversion has been enabled.
4. Repeat with multiple registered labels and the deployment's keyboard layout.

The app does not assume that Data Matrix means binary USB input, request browser USB permissions, or infer the payload from the product name. Installed Pi stations read the scanner through the local input service; hosted pages use keyboard capture. Both pass the same code to the server, which verifies the check digit, and neither treats a scanner beep as database confirmation. See the [scanner implementation guide](SCANNER_IMPLEMENTATION_GUIDE.md) for capture, startup, and recovery.

## Layout and stock

Labels are printed on the Brother QL-810W using **Brother DK-1209 die-cut labels, 62 mm wide × 29 mm long** (2.4 × 1.1 in), one label per PDF page. Brother's raster reference for this printer gives a 58.9 mm printable width (1.5 mm minimum side margins) and a 3 mm minimum margin at each label's leading and trailing edge. Every label keeps a **3 mm margin on all four sides**. The left side holds the 19.4 mm Data Matrix; the right side shows project, material, test, treatment, "Piece n / N", and the label code as "No.". The label length is fixed, so text that would not fit at 7 pt is printed smaller (down to 4 pt) instead of lengthening the label. [Brother QL-800/810W/820NWB Raster Command Reference](https://download.brother.com/welcome/docp100278/cv_ql800_eng_raster_101.pdf)

The job is sent as `media=Custom.62x29mm` with `CutMedia=EndOfPage`, so the printer cuts after every label. Staff do not choose a cut mode. The 62 × 29 mm layout has not yet been test-printed on a DK-1209 roll; print one label and scan it before relying on it.

The QL-810W is a direct-thermal printer. Turn off its Editor Lite mode (green LED); otherwise it appears as a USB drive instead of a printer. The application prints black only. Adhesion, heat/laundering durability, and fabric compatibility have not been established by software tests.

## Preview, then print

1. Join the printer and Mac to a reachable network, or connect by USB. Add the Brother printer in macOS Printers & Scanners using a supported driver/queue and the actual roll. Wi-Fi capability alone does not create that Mac queue. [Apple: add and manage printers](https://support.apple.com/en-gb/guide/mac-help/mh14004/27/mac/27)
2. Register the reviewed prep rows. Select the registered stacks and open the label preview.
3. Check readable fields, numbered pieces, count, dimensions, and the Data Matrix margin. Save/download the PDF if direct printing is not configured.
4. Select the existing stacks or numbered specimens to print. Preview and Print use exactly this same selection; reprints retain the original IDs and specimen numbers. Choose an available Mac printer queue and press Print. The Mac service submits the previewed labels through CUPS using the selected custom media dimensions and no fit-to-page scaling.
5. Inspect the physical result and scan representative labels back into the application.

The printer queue is installed on the host Mac; office browsers ask that host to submit the job. They do not need to install their own printer drivers to use the app's direct Print action. PDF download remains available if CUPS/the queue is unavailable or the chosen roll needs a manual print workflow.

## Print status and lost responses

A durable print-request ID identifies each submission. Retrying the same request and unchanged settings returns its recorded state instead of deliberately sending a duplicate CUPS job. Changed stacks, dimensions, queue, or copies need a new request.

**Submitted** means CUPS accepted the job. It does not prove that paper emerged, the printer cut correctly, or the label was attached to the intended specimen. A submitting/unknown result after an interrupted response may already have reached the queue; inspect the queue and physical output before intentionally creating a new print job. Reprinting keeps the original specimen IDs.

PDF preview and CSV export are interchangeable outputs from registered records. CSV contains each label code as text.

## Physical acceptance checklist

- Confirm the installed roll, PDF media size, cutter behavior, orientation, and scale.
- Check text readability on the smallest actual specimen and leave the full quiet zone clear.
- Scan first/middle/last labels from representative stacks.
- Compare decoded label codes and specimen numbers with registered records.
- Confirm that duplicate/reprinted labels refer to the same specimen.
- Test adhesion and readability through the lab's actual handling conditions.
- Record first-read accuracy and final-Enter-to-confirmation latency on the Pi/Eyoyo/printed-stock combination.

Rendered-code decoding and mocked CUPS tests validate software behavior; they do not replace these physical checks.
