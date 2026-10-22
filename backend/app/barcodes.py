"""Lossless 128-bit IDs in keyboard-friendly ECC200 Data Matrix symbols.

Decimal is intentional: ECC200 packs two decimal digits per data codeword. It
uses a 20x20 symbol for the full unsigned 128-bit range, while preserving USB
keyboard transport without control characters or keyboard-layout punctuation.
"""
from __future__ import annotations

import io
from uuid import UUID

from PIL import Image
import zxingcpp


def id_payload(identifier: str) -> str:
    """Return all 128 identifier bits as exactly 39 decimal characters."""
    return f"{UUID(str(identifier)).int:039d}"


def payload_id(payload: str) -> str:
    text = str(payload).strip()
    if not text.isascii() or not text.isdecimal() or not 1 <= len(text) <= 39:
        raise ValueError("Expected an unsigned 128-bit decimal identifier.")
    number = int(text)
    if number >= 1 << 128:
        raise ValueError("Identifier exceeds 128 bits.")
    return str(UUID(int=number))


def datamatrix_image(payload: str, module_pixels: int = 10) -> Image.Image:
    """Render crisp modules with the required one-module quiet zone."""
    # ID validation prevents an accidental URL, prefix, or prep data payload.
    payload_id(payload)
    code = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.DataMatrix)
    bitmap = memoryview(zxingcpp.write_barcode_to_image(
        code, scale=1, add_hrt=False, add_quiet_zones=True))
    height, width = bitmap.shape
    image = Image.frombytes("L", (width, height), bitmap.tobytes())
    return image.resize((width * module_pixels, height * module_pixels), Image.Resampling.NEAREST)


def datamatrix_png(identifier: str) -> bytes:
    output = io.BytesIO()
    datamatrix_image(id_payload(identifier)).save(output, format="PNG")
    return output.getvalue()
