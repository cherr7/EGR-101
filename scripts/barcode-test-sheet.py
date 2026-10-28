#!/usr/bin/env python3
"""Print a scanner acceptance sheet: production Data Matrix IDs plus keyboard-mapping checks.

Page 1 uses the app's own label encoder (backend/app/barcodes.py): specimen numbers plus a
check digit in a fixed 16 x 16 symbol, at the 19.4 mm size the 62 x 25.4 mm labels print.
Scanning each into a blank editor must type exactly the digits shown, then one Enter.

Page 2 checks scanner setup with other symbologies: case, punctuation, and Code 39/EAN
handling. Those payloads are not specimen IDs; the kiosk should reject them.

Every Data Matrix is decoded again before the PDF is written, so a sheet that prints is a
sheet whose symbols match their captions.

    python3 scripts/barcode-test-sheet.py [--output PATH]
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PIL import Image
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code39, code128, eanbc, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.barcodes import MAX_ID, datamatrix_image, label_code  # noqa: E402

LABEL_CODE_SIZE = 19.4 * mm  # matches labels_pdf() on the default 62 x 25.4 mm label

# (specimen number, printed size, note)
SPECIMEN_TILES = [
    (1, LABEL_CODE_SIZE, "First specimen: shortest code (2 digits)"),
    (42, LABEL_CODE_SIZE, "Two-digit number"),
    (1000000000, LABEL_CODE_SIZE, "Zeros inside the number must survive"),
    (4815162342, LABEL_CODE_SIZE, "Typical ten-digit number"),
    (MAX_ID, LABEL_CODE_SIZE, "Largest bigint: longest code (20 digits)"),
    (4815162342, 30 * mm, "Same code enlarged, for aiming/distance checks"),
]

# (symbology, payload, note)
SETUP_TILES = [
    ("Code 128", "UL-FAB-0001", "Alphanumeric with hyphens"),
    ("Code 128", "8675309", "Numeric only"),
    ("Code 128", "MixedCase_abc", "Checks case / Caps Lock mapping"),
    ("Code 128", "Test 42!", "Space and punctuation"),
    ("Code 39", "ABC-123", "Uppercase-only symbology"),
    ("EAN-13", "4006381333931", "Retail numeric, valid check digit"),
    ("QR (2D)", "UL-FAB-0002|IN|2026-10-22", "2D imagers only"),
    ("Data Matrix (2D)", "UL-FAB-0003", "Non-ID text: kiosk must reject it"),
]

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch
COLS, ROWS = 2, 4
TILE_W = (PAGE_W - 2 * MARGIN) / COLS
TILE_H = (PAGE_H - 2 * MARGIN - 0.6 * inch) / ROWS


def checked_datamatrix(payload, validate_id=True):
    """Render a Data Matrix and confirm it decodes to exactly `payload`."""
    if validate_id:
        image = datamatrix_image(payload)
    else:  # same rendering as datamatrix_image(), minus its specimen-ID check
        code = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.DataMatrix)
        bitmap = memoryview(zxingcpp.write_barcode_to_image(code, scale=1, add_hrt=False, add_quiet_zones=True))
        height, width = bitmap.shape
        image = Image.frombytes("L", (width, height), bitmap.tobytes())
        image = image.resize((width * 10, height * 10), Image.Resampling.NEAREST)
    decoded = zxingcpp.read_barcode(image)
    if decoded is None or decoded.text != payload:
        raise RuntimeError(f"Data Matrix for {payload!r} decoded as {decoded and decoded.text!r}")
    return image.convert("RGB")


def setup_symbol(kind, data):
    """Return a reportlab drawable or PIL image for a setup tile, with its (w, h)."""
    if kind == "Code 128":
        b = code128.Code128(data, barHeight=0.7 * inch, barWidth=0.015 * inch, humanReadable=False)
        return b, b.width, b.height
    if kind == "Code 39":
        b = code39.Standard39(data, barHeight=0.7 * inch, barWidth=0.015 * inch,
                              checksum=0, stop=1, humanReadable=False)
        return b, b.width, b.height
    if kind == "EAN-13":
        w = eanbc.Ean13BarcodeWidget(data[:12])  # library computes the check digit
        w.barHeight, w.barWidth, w.humanReadable = 0.7 * inch, 0.015 * inch, 0
        x0, y0, x1, y1 = w.getBounds()
        d = Drawing(x1 - x0, y1 - y0)
        w.x, w.y = -x0, -y0
        d.add(w)
        return d, d.width, d.height
    if kind == "QR (2D)":
        size = 1.15 * inch
        w = qr.QrCodeWidget(data, barLevel="M")
        x0, y0, x1, y1 = w.getBounds()
        d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        d.add(w)
        return d, size, size
    if kind == "Data Matrix (2D)":
        return checked_datamatrix(data, validate_id=False), inch, inch
    raise ValueError(kind)


def draw_tile(c, x, y, heading, symbol, sw, sh, caption, note):
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.setDash(3, 3)
    c.rect(x + 4, y + 4, TILE_W - 8, TILE_H - 8)
    c.setDash()
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.setFont("Helvetica", 8)
    c.drawString(x + 14, y + TILE_H - 20, heading.upper())

    sx = x + (TILE_W - sw) / 2
    sy = y + 44 + (TILE_H - 44 - 28 - sh) / 2
    if isinstance(symbol, Drawing):
        renderPDF.draw(symbol, c, sx, sy)
    elif hasattr(symbol, "drawOn"):
        symbol.drawOn(c, sx, sy)
    else:
        c.drawImage(ImageReader(symbol), sx, sy, width=sw, height=sh, preserveAspectRatio=True)

    font_size = 11
    while c.stringWidth(caption, "Courier-Bold", font_size) > TILE_W - 24:
        font_size -= 0.5
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Courier-Bold", font_size)
    c.drawCentredString(x + TILE_W / 2, y + 26, caption)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.setFont("Helvetica", 7.5)
    c.drawCentredString(x + TILE_W / 2, y + 14, note)


def page_header(c, title, subtitle):
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(MARGIN, PAGE_H - MARGIN - 4, title)
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.drawString(MARGIN, PAGE_H - MARGIN - 18, subtitle)


def tile_origin(i):
    col, row = i % COLS, i // COLS
    return MARGIN + col * TILE_W, PAGE_H - MARGIN - 0.6 * inch - (row + 1) * TILE_H


def build(path):
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Scanner Acceptance Sheet")

    page_header(c, "Scanner Acceptance Sheet: specimen IDs",
                "Scan each into a blank editor. Output must be exactly the digits shown, then one Enter.")
    for i, (identifier, size, note) in enumerate(SPECIMEN_TILES):
        payload = label_code(identifier)
        image = checked_datamatrix(payload)
        heading = f"Data Matrix, {size / mm:.1f} mm" + (" (label size)" if size == LABEL_CODE_SIZE else "")
        draw_tile(c, *tile_origin(i), heading, image, size, size, payload, note)
    c.showPage()

    page_header(c, "Scanner Acceptance Sheet: keyboard mapping",
                "Scan each into a blank editor; output must match the bold text exactly. These are not specimen IDs.")
    for i, (kind, data, note) in enumerate(SETUP_TILES):
        symbol, sw, sh = setup_symbol(kind, data)
        draw_tile(c, *tile_origin(i), kind, symbol, sw, sh, data, note)
    c.showPage()
    c.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=ROOT / "output/qa/barcode-test-sheet.pdf")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    build(args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
