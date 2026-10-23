# Specimen IDs, Data Matrix labels, and Brother printing

## Identity and label payload

Each physical piece receives one permanent 128-bit UUID when its reviewed prep row is registered. A separate stack record groups the individually numbered pieces. Project numbers and customer/material references remain ordinary prep metadata; they are not generated specimen IDs.

The only encoded label content is the specimen ID, represented as **exactly 39 zero-padded decimal digits**. This is a lossless representation of the 128-bit integer value. It contains no URL, project data, prefix, treatment, specimen count, or Enter character. The QR/UL1/binary formats found in earlier prototype galleries are obsolete label defaults.

The encoder produces ordinary **ECC200 Data Matrix**, not a GS1 application-identifier payload. Decimal pairs are compactly encoded. The current full-range payload fits a **20 × 20 module symbol**, plus a one-module clear quiet zone on every side: a 22 × 22 module footprint including that margin. The generator and decoder tests check the actual payload. GS1's technical guideline documents these symbol capacities and quiet zones; that reference does not make this app's private IDs GS1 identifiers. [GS1 DataMatrix guideline](https://ref.gs1.org/guidelines/datamatrix/)

A symbol also needs finder/timing patterns and error correction. Those are required structure rather than unused space to fill with unrelated information. Decimal transport preserves the full ID value while avoiding arbitrary raw bytes/control characters in a USB keyboard stream.

Keep identifiers as strings in JavaScript and JSON. A 128-bit ID exceeds the exact integer range of JavaScript Number; use BigInt only for explicit conversions and serialize back to a string. The backend resolves the decimal value to its registered UUID and stack. Reprinting reuses those identities and does not create another physical specimen.

## Scanner transport

The Eyoyo EYH2 is used as a USB keyboard-wedge scanner: decoding a label should type its decimal payload followed by Enter. Enter is a scanner suffix setting, not part of the barcode. Print the acceptance sheet with `python3 scripts/barcode-test-sheet.py`, which writes `output/qa/barcode-test-sheet.pdf`. Page 1 has production-encoder Data Matrix IDs at the 18.6 mm label size, including all-zero, leading-zero and maximum values. Page 2 has Code 128, Code 39, EAN-13, QR and Data Matrix keyboard-mapping checks. The script decodes every Data Matrix before writing the PDF. Configure and verify the actual device with a simple text editor before relying on the kiosk:

1. Scan an app-generated label into a blank editor.
2. Confirm exactly 39 digits, including leading zeroes, and then one Enter.
3. Check that no extra prefix/suffix, tab, character substitution, or conversion has been enabled.
4. Repeat with multiple registered labels and the deployment's keyboard layout.

The app does not assume that Data Matrix means binary USB input, request browser USB permissions, or infer the payload from the product name. Actual scanner configuration and physical first-read performance remain hardware acceptance work. See the [scanner implementation guide](SCANNER_IMPLEMENTATION_GUIDE.md) for field-free capture and reboot recovery.

## Layout and stock

The default PDF is **2 inches wide × 1 inch high**, or **50.8 × 25.4 mm**, with one label per PDF page. It shows the code alongside readable project, material, test, exact treatment text, and numbered piece/count. The short readable ID is a visual aid; the complete encoded ID remains in the Data Matrix.

The Brother QL-810W is a direct-thermal printer supporting USB and Wi-Fi. Brother lists starter DK-1201 address labels and a 62 mm continuous black/red-on-white roll in the box; these are not guaranteed 1 × 2 inch die-cut stock. Check the actual supplied rolls and configured media before printing the default layout. [Brother QL-810W specifications and carton contents](https://www.brother-usa.com/p/desktop-label-printers/QL810W?pid=QL810W)

The requested 1 × 2 content layout is suitable for previewing small specimens, but the physical label page must match the installed Brother-compatible roll and cutter settings. A continuous roll has its own fixed width; changing a PDF page size does not change that width. Use the app's adjustable dimensions for the actual stock and inspect a first print at 100% scale.

The application prints black codes/text. It has no red-channel workflow. Paper adhesion, heat/laundering durability, fabric compatibility, and whether labels remain attached during the lab's actual prep/test process have not been established by software tests.

## Preview, then print

1. Join the printer and Mac to a reachable network, or connect by USB. Add the Brother printer in macOS Printers & Scanners using a supported driver/queue and the actual roll. Wi-Fi capability alone does not create that Mac queue. [Apple: add and manage printers](https://support.apple.com/en-gb/guide/mac-help/mh14004/27/mac/27)
2. Register the reviewed prep rows. Select the registered stacks and open the label preview.
3. Check readable fields, numbered pieces, count, dimensions, and the Data Matrix margin. Save/download the PDF if direct printing is not configured.
4. Select the existing stacks or numbered specimens to print. Preview and Print use exactly this same selection; reprints retain the original IDs and specimen numbers. Choose an available Mac printer queue and press Print. The Mac service submits the previewed labels through CUPS using the selected custom media dimensions and no fit-to-page scaling.
5. Inspect the physical result and scan representative labels back into the application.

The printer queue is installed on the host Mac; office browsers ask that host to submit the job. They do not need to install their own printer drivers to use the app's direct Print action. PDF download remains available if CUPS/the queue is unavailable or the chosen roll needs a manual print workflow.

## Print status and lost responses

A durable print-request UUID identifies each submission. Retrying the same request and unchanged settings returns its recorded state instead of deliberately sending a duplicate CUPS job. Changed stacks, dimensions, queue, or copies need a new request.

**Submitted** means CUPS accepted the job. It does not prove that paper emerged, the printer cut correctly, or the label was attached to the intended specimen. A submitting/unknown result after an interrupted response may already have reached the queue; inspect the queue and physical output before intentionally creating a new print job. Reprinting keeps the original specimen IDs.

PDF preview and CSV export are interchangeable outputs from registered records. CSV contains the full ID payload as text; opening it in spreadsheet software must not convert the long decimal string into a floating-point number. Use text import/column formatting when working with that payload.

## Physical acceptance checklist

- Confirm the installed roll, PDF media size, cutter behavior, orientation, and scale.
- Check text readability on the smallest actual specimen and leave the full quiet zone clear.
- Scan first/middle/last labels from representative stacks, including labels with leading-zero payloads.
- Compare decoded IDs and specimen numbers with registered records.
- Confirm that duplicate/reprinted labels refer to the same specimen.
- Test adhesion and readability through the lab's actual handling conditions.
- Record first-read accuracy and final-Enter-to-confirmation latency on the Pi/Eyoyo/printed-stock combination.

Rendered-code decoding and mocked CUPS tests validate software behavior; they do not replace these physical checks.
