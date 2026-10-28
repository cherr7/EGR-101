"""Sequential label numbers with a Damm check digit, in fixed 16 x 16 ECC200 Data Matrix symbols.

A specimen's label code is its database number followed by one Damm check digit,
which catches every single-digit typo and every swap of neighbouring digits when
someone types a code from a damaged label. Digits only: a keyboard-wedge scanner
types them identically on every layout, and ECC200 packs two digits per codeword.
"""
from __future__ import annotations

from PIL import Image
import zxingcpp

MAX_ID = 2 ** 63 - 1          # PostgreSQL bigint
SYMBOL_VERSION = 4            # 16 x 16 square: holds 24 digits, so every bigint code fits one fixed size
DAMM = ("0317598642", "7092154863", "4206871359", "1750983426", "6123045978",
        "3674209581", "5869720134", "8945362017", "9438617205", "2581436790")


def damm(digits: str) -> int:
    interim = 0
    for digit in digits:
        interim = int(DAMM[interim][int(digit)])
    return interim


def label_code(identifier: int) -> str:
    """The scanned and printed form of a specimen number: the number plus its check digit."""
    number = str(int(identifier))
    return number + str(damm(number))


def code_id(code: str) -> int:
    """Return the specimen number in a label code, or raise ValueError for a malformed or mistyped code."""
    text = str(code).strip()
    if not text.isascii() or not text.isdecimal() or not 2 <= len(text) <= 20:
        raise ValueError("Expected a label number and its check digit.")
    if damm(text):
        raise ValueError("Check digit does not match. Rescan or retype the label.")
    number = int(text[:-1])
    if not 1 <= number <= MAX_ID:
        raise ValueError("Label number is out of range.")
    return number


def datamatrix_image(payload: str, module_pixels: int = 10) -> Image.Image:
    """Render crisp modules with the required one-module quiet zone."""
    code_id(payload)  # Never encode a URL, prefix, or prep data by accident.
    code = zxingcpp.create_barcode(payload, zxingcpp.BarcodeFormat.DataMatrix, version=SYMBOL_VERSION)
    bitmap = memoryview(zxingcpp.write_barcode_to_image(
        code, scale=1, add_hrt=False, add_quiet_zones=True))
    height, width = bitmap.shape
    image = Image.frombytes("L", (width, height), bitmap.tobytes())
    return image.resize((width * module_pixels, height * module_pixels), Image.Resampling.NEAREST)
