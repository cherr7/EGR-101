# Printer setup and labels

## Who talks to the printer

Only the **Mac** talks to the printer. When anyone presses Print in the staff app, the browser asks the Mac, and the Mac sends the job to its own macOS printer queue. Staff computers don't need printer drivers, and **the Pis never need the printer.**

```
staff browser ──▶ Mac (port 8120) ──▶ macOS printer queue ──USB or Wi-Fi──▶ QL-810W
```

So the only requirement is: **the Mac must be able to reach the printer**, either by a USB cable or because both are on the same network.

## What to plug in

| Cable | Needed? |
| --- | --- |
| Printer power (AC adapter to wall) | **Always.** The QL-810W has no battery. |
| USB from printer to Mac | Only if you connect by USB. The printer has a USB-B port and the MacBook Neo has USB-C, so use a USB-C to USB-B cable or an adapter. Not needed for Wi-Fi. |
| Network cable | No. The QL-810W has no Ethernet port; it's USB or Wi-Fi. |

**Turn Editor Lite off.** If the green Editor Lite light is on, the printer shows up on the Mac as a USB drive instead of a printer. Hold the Editor Lite button until the green light goes off.

## Option A: USB (simplest, recommended if the printer sits next to the Mac)

1. Plug in printer power, turn it on, make sure Editor Lite is off.
2. Connect the USB cable to the Mac.
3. On the Mac: **System Settings → Printers & Scanners → Add Printer, Scanner or Fax…**
4. Select **Brother QL-810W** (Kind: USB). Under **Use**, AirPrint is fine to start (see "AirPrint or Brother driver" below). Click Add.
5. Check it worked; see "Check the Mac can print" below.

## Option B: Wi-Fi

Wi-Fi only helps if the printer needs to sit away from the Mac. The printer and Mac must be on the **same network** (same Wi-Fi/subnet) for the Mac to find it automatically. Staff computers and Pis don't need to reach it.

**Join the printer to Wi-Fi.** Pick whichever your network allows:

- **WPS (home-style router with a WPS button):** press WPS on the router, then within two minutes hold the printer's **Wi-Fi** button for about five seconds until the Wi-Fi light starts flashing. When it stays lit, it's connected.
- **No WPS (most campus/office networks):** connect the printer to the Mac by USB first, then use Brother's wireless setup tool for the QL-810W from [Brother's download page](https://support.brother.com/) (choose QL-810W and your macOS version) to enter the network name and password. Then unplug the USB cable.
- **Enterprise Wi-Fi (username + password logins such as eduroam) or a network with a sign-in web page:** the printer usually can't join these. Use USB (Option A), or ask IT for a device network and a reserved address for the printer.

**Add it on the Mac:**

1. **System Settings → Printers & Scanners → Add Printer, Scanner or Fax…**
2. It should appear as **Brother QL-810W** (Kind: Bonjour). Select it and click Add.
3. If it doesn't appear but the printer is on the network (for example on a different subnet), use the **IP** tab: enter the printer's IP address, Protocol **IPP**, then Add. Ask IT for a reserved address so it doesn't change.

### Can I just use AirPrint if they're on the same Wi-Fi?

Yes, to get started. Brother lists the QL-810W as AirPrint-capable, so macOS can add it without installing anything, by USB or Wi-Fi. The app prints by sending each label to the Mac queue with these options:

```
lp -d <queue> -o media=Custom.62x29mm -o CutMedia=EndOfPage …
```

On the host MacBook Neo (macOS 27.2), plugging the printer in by USB created the queue `Brother_QL_810W` automatically. It is an AirPrint queue over USB (`ippusb://…`, driver "Brother QL-810W-AirPrint"), and it was set as the default printer. That queue offers `CutMedia` = None / **EndOfPage** (default) / EndOfJob, and accepts `Custom.WIDTHxHEIGHT` page sizes, so the app's options are recognised. No Brother software is installed. Whether the printer actually cuts and sizes 62 × 29 mm correctly is confirmed only by the first physical print. If labels come out the wrong size, scaled, or uncut, install Brother's **CUPS driver** for the QL-810W from Brother's download page, remove the queue, add the printer again, and under **Use** pick the Brother QL-810W driver instead of AirPrint.

## Check the Mac can print

1. In Terminal: `lpstat -p` should list the queue (for example `Brother_QL_810W`) as idle/enabled.
2. Start the app and open **http://127.0.0.1:8120/api/printers**. The queue should be listed. The same list appears in the app's Print dialog.
3. Print one label from the app (see "Label rolls" below for which roll), then scan it with the Eyoyo into a blank text editor. You should get exactly the digits printed after "No." plus Enter.

If the app says CUPS is unavailable, the Mac has no printer queue yet. PDF preview and download still work.

## Label rolls

The app's labels are laid out for **DK-1209: 62 mm wide × 29 mm long die-cut**, one label per page, with a cut after each one.

| Roll | Works with the app? | Use it for |
| --- | --- | --- |
| **DK-1209** 62 × 29 mm die-cut | Yes, this is the target roll. | Real use. |
| **DK-2251** 62 mm continuous, red/black | Yes, for testing. Same 62 mm width, so app labels print at the real layout and the cutter cuts every 29 mm instead of at die-cut gaps. The app prints black only. | **Testing now**, before DK-1209 arrives. Each label uses about 29 mm of tape (roughly 34 per metre), so print 1–3 at a time if the roll is nearly empty. |
| **DK-1201** 29 × 90 mm die-cut address labels | No. It's 29 mm wide, not 62 mm, so the app's 62 × 29 mm page doesn't fit. Expect a size error or a clipped label. | A plain "can the Mac reach the printer" check only: print the macOS queue's test page or a small TextEdit note with the paper size set to the 29 × 90 mm label. |

Swapping rolls: open the top cover, lift out the roll with its spool, drop the new one in so the guide sits in the slot, feed the end to the cutter, close the cover. The printer reads the roll type itself.

**Test order with what you have now:** add the queue (USB or Wi-Fi) → check `lpstat -p` and `/api/printers` → load the DK-2251 and print one label from the app → scan it. When DK-1209 arrives, repeat once and run the physical checklist at the bottom.

The QL-810W is direct thermal: no ink or ribbon. Labels can fade with heat and light, and their adhesion to fabric through laundering or heat has not been tested.

## What the label contains

PostgreSQL numbers every stack and every piece sequentially when a reviewed prep row is registered. A label carries one thing: the **specimen's label code**, its number followed by one [Damm](https://en.wikipedia.org/wiki/Damm_algorithm) check digit. Specimen 12345 is `123459`. The check digit catches every single wrong digit and every swap of two neighbouring digits, so a code typed from a damaged label can't land on another real specimen. Codes are 2 to 20 digits and contain no URL, project, prefix or Enter. Stacks have no labels of their own; scanning any piece finds its stack.

The symbol is a standard **ECC200 Data Matrix**, always **16 × 16 modules** plus a one-module quiet zone, whatever the code length. Digits only, because a USB keyboard scanner can mangle punctuation through keyboard layouts and Caps Lock. Reprinting reuses the same number and doesn't create another specimen.

**Layout:** 3 mm margin on all four sides (Brother's minimum is 1.5 mm at the sides and 3 mm at each end). The 19.4 mm Data Matrix is on the left. The right side shows project, material, test, treatment, "Piece n / N", and the code as "No.". Text that won't fit at 7 pt shrinks (down to 4 pt) instead of making the label longer. Reference: [Brother QL-800/810W/820NWB Raster Command Reference](https://download.brother.com/welcome/docp100278/cv_ql800_eng_raster_101.pdf).

## Scanner check

The Eyoyo EYH2 acts as a USB keyboard: a scan types the digits, then Enter. `python3 scripts/barcode-test-sheet.py` writes `output/qa/barcode-test-sheet.pdf`. Page 1 has real label codes at the 19.4 mm size, shortest to longest; page 2 has Code 128, Code 39, EAN-13, QR and Data Matrix checks. Scan into a blank text editor and confirm exactly the digits, one Enter, no extra prefix/suffix or substitutions.

## Print status

Each submission has a durable print-request ID. Retrying the same request returns its recorded state instead of sending a duplicate job. **Submitted** means CUPS accepted the job; it doesn't prove a label came out, cut correctly or went on the right specimen. After an interrupted response, check the queue and the printer before printing again. A registered job can preview or print up to 1,000 stacks or 5,000 labels at once.

## Physical acceptance checklist (on DK-1209)

- Roll, page size, cutter behaviour, orientation and scale are right.
- Text is readable and the Data Matrix quiet zone is clear.
- First, middle and last labels of a few stacks scan to the right specimen.
- Reprints refer to the same specimen.
- Labels stay on and stay readable through the lab's handling.
- First-read rate and final-Enter-to-green time on the Pi/Eyoyo/printed stock.

Software tests decode the rendered labels and mock CUPS; they don't replace these checks.
