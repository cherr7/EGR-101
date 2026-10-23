#!/usr/bin/env python3
"""Type specimen IDs read from NFC tags into the kiosk, exactly as the barcode scanner would.

A PN532 hat (SPI) reads the NDEF text record on an NTAG21x tag. When that text is a
specimen ID, the bridge types it through a virtual keyboard (/dev/uinput) as 39 decimal
digits followed by Enter: the same keystrokes as a scanned Data Matrix label. The kiosk's
page-wide capture listener therefore handles NFC taps with no frontend changes, and all
movement rules, health checks, and confirmations stay in the React app and API.

    nfc-bridge.py              run the bridge (fabric-scanner-nfc.service)
    nfc-bridge.py read         print each tapped tag's ID without typing it
    nfc-bridge.py write ID     write a specimen ID (39 digits or UUID) to one NTAG tag

Hat wiring: I1=H, I0=L, RSTPDN->D20, DIP 1-4 (SCK/MISO/MOSI/D4) ON, 5-8 (SCL/SDA/TX/RX) OFF.
"""
import sys
import time
from uuid import UUID

POLL_TIMEOUT = 0.1    # seconds per detection attempt
REARM_AFTER = 0.75    # a tag must be off the coil this long before it types again
RETRY_DELAY = 2.0     # seconds before reconnecting a hat that failed
KEY_DELAY = 0.004     # seconds between key events; the kiosk rejects gaps over 350 ms
ID_DIGITS = 39


# --- specimen IDs -----------------------------------------------------------

def normalize_id(text):
    """Return the 39-digit decimal payload for a decimal or UUID specimen ID, else raise ValueError."""
    value = str(text).strip()
    if value.isascii() and value.isdecimal() and 1 <= len(value) <= ID_DIGITS:
        number = int(value)
    else:
        try:
            number = UUID(value).int
        except ValueError:
            raise ValueError(f"not a specimen ID: {value[:60]!r}") from None
    if number >= 1 << 128:
        raise ValueError("specimen ID exceeds 128 bits")
    return f"{number:0{ID_DIGITS}d}"


class TagFilter:
    """Report each tag once per tap, ignoring repeat reads while it rests on the coil."""

    def __init__(self, rearm_after=REARM_AFTER):
        self.rearm_after = rearm_after
        self.last_uid, self.last_seen = None, float("-inf")

    def is_new_tap(self, uid, now):
        repeat = uid == self.last_uid and now - self.last_seen < self.rearm_after
        self.last_uid, self.last_seen = uid, now
        return not repeat


# --- NDEF on NTAG21x --------------------------------------------------------

def encode_ndef_text(text, lang="en"):
    payload = bytes([len(lang)]) + lang.encode() + text.encode("utf-8")
    record = bytes([0xD1, 0x01, len(payload)]) + b"T" + payload  # MB|ME|SR, TNF=well-known
    if len(record) > 254:
        raise ValueError("text too long for a short NDEF record")
    tlv = bytes([0x03, len(record)]) + record + b"\xFE"
    return tlv + b"\x00" * (-len(tlv) % 4)  # pad to whole 4-byte pages


def read16(pn532, page):
    """One NTAG READ returns 16 bytes (4 pages)."""
    chunk = pn532.mifare_classic_read_block(page)
    if chunk is None or len(chunk) != 16:
        raise RuntimeError(f"read failed at page {page}")
    return bytes(chunk)


def read_ndef_message(pn532):
    """Return the raw NDEF message from an NTAG, b"" if empty, or None if not NDEF-formatted."""
    cc = read16(pn532, 3)[:4]
    if cc[0] != 0xE1:
        return None
    capacity = cc[2] * 8
    data, page = bytearray(), 4

    def ensure(n):
        nonlocal page
        if n > capacity:
            raise ValueError("NDEF length exceeds the tag's declared capacity")
        while len(data) < n:
            data.extend(read16(pn532, page))
            page += 4

    i = 0
    while i < capacity:
        ensure(i + 1)
        tag = data[i]
        if tag == 0x00:                 # NULL TLV: padding
            i += 1
            continue
        if tag == 0xFE:                 # terminator: no NDEF message
            return b""
        ensure(i + 2)
        if data[i + 1] == 0xFF:         # 3-byte length form
            ensure(i + 4)
            length, header = int.from_bytes(data[i + 2:i + 4], "big"), 4
        else:
            length, header = data[i + 1], 2
        ensure(i + header + length)
        if tag == 0x03:                 # NDEF message TLV
            return bytes(data[i + header:i + header + length])
        i += header + length            # skip lock/memory-control TLVs
    return b""


def decode_text_record(message):
    """Return the first record's text if it is an NDEF text record, else None."""
    if len(message) < 3:
        return None
    flags, type_len = message[0], message[1]
    if flags & 0x07 != 0x01 or not flags & 0x80 or flags & 0x20:
        return None
    if flags & 0x10:
        length, p = message[2], 3
    else:
        if len(message) < 6:
            return None
        length, p = int.from_bytes(message[2:6], "big"), 6
    if flags & 0x08 and p >= len(message):
        return None
    id_len = message[p] if flags & 0x08 else 0
    p += 1 if flags & 0x08 else 0
    if p + type_len + id_len + length > len(message):
        return None
    record_type = bytes(message[p:p + type_len])
    p += type_len + id_len
    payload = bytes(message[p:p + length])
    if record_type != b"T" or not payload:
        return None
    status = payload[0]
    start = 1 + (status & 0x3F)
    if status & 0x40 or start > len(payload):
        return None
    try:
        return payload[start:].decode("utf-16" if status & 0x80 else "utf-8")
    except UnicodeError:
        return None


def read_tag_id(pn532, uid):
    """Return the 39-digit specimen ID stored on a tag, or raise ValueError/RuntimeError."""
    if len(uid) != 7:
        raise ValueError("not an NTAG21x tag (expected a 7-byte UID)")
    message = read_ndef_message(pn532)
    if message is None:
        raise ValueError("tag is not NDEF-formatted")
    text = decode_text_record(message)
    if text is None:
        raise ValueError("tag holds no NDEF text record")
    return normalize_id(text)


def write_tag_id(pn532, uid, identifier):
    """Write a specimen ID as an NDEF text record and verify it reads back."""
    payload = normalize_id(identifier)
    message = encode_ndef_text(payload)
    if len(uid) != 7:
        raise ValueError("writing only supports NTAG21x tags (7-byte UID)")
    cc = read16(pn532, 3)[:4]
    if cc[0] != 0xE1:
        raise ValueError("tag is not NDEF-formatted; refusing to write")
    if len(message) > cc[2] * 8:
        raise ValueError(f"message is {len(message)} bytes; tag holds {cc[2] * 8}")
    for offset in range(0, len(message), 4):
        if not pn532.ntag2xx_write_block(4 + offset // 4, message[offset:offset + 4]):
            raise RuntimeError(f"write failed at page {4 + offset // 4}; hold the tag still and retry")
    if read_tag_id(pn532, uid) != payload:
        raise RuntimeError("written tag did not read back the same ID")
    return payload


# --- hardware ---------------------------------------------------------------

def connect():
    """Open the PN532 over SPI and configure it to read tags."""
    import board
    import busio
    from adafruit_pn532.spi import PN532_SPI
    from digitalio import DigitalInOut

    spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    pn532 = PN532_SPI(spi, DigitalInOut(board.D4), reset=DigitalInOut(board.D20))
    _, version, revision, _ = pn532.firmware_version
    log(f"PN532 firmware {version}.{revision}")
    pn532.SAM_configuration()
    return pn532


class Keyboard:
    """A virtual keyboard that only types digits and Enter."""

    def __init__(self, device=None):
        from evdev import UInput, ecodes
        self.ecodes = ecodes
        self.keys = {str(d): getattr(ecodes, f"KEY_{d}") for d in range(10)}
        self.device = device or UInput({ecodes.EV_KEY: [*self.keys.values(), ecodes.KEY_ENTER]},
                                       name="fabric-scanner-nfc")
        if device is None:
            time.sleep(1)  # give the compositor time to adopt the new input device

    def press(self, code):
        for value in (1, 0):  # key down, key up
            self.device.write(self.ecodes.EV_KEY, code, value)
            self.device.syn()
            time.sleep(KEY_DELAY)

    def type_line(self, digits):
        for ch in digits:
            self.press(self.keys[ch])
        self.press(self.ecodes.KEY_ENTER)


def log(message):
    print(message, flush=True)


def taps(pn532, tag_filter=None, clock=time.monotonic):
    """Yield (uid, specimen ID or None, error or None) once per tag tap."""
    tag_filter = tag_filter or TagFilter()
    while True:
        try:
            uid = pn532.read_passive_target(timeout=POLL_TIMEOUT)
        except RuntimeError:
            continue  # garbled frame from a tag at the edge of range
        if not uid:
            continue
        uid = bytes(uid)
        if not tag_filter.is_new_tap(uid, clock()):
            continue
        try:
            yield uid, read_tag_id(pn532, uid), None
        except (ValueError, RuntimeError) as error:
            yield uid, None, str(error)


def run(type_ids=True):
    keyboard = Keyboard() if type_ids else None
    while True:
        try:
            pn532 = connect()
            log("Waiting for NFC tags...")
            for uid, specimen_id, error in taps(pn532):
                if error:
                    log(f"{uid.hex(':')}  ignored: {error}")
                    continue
                log(f"{uid.hex(':')}  {specimen_id}")
                if keyboard:
                    keyboard.type_line(specimen_id)
        except Exception as error:  # SPI/hat failure: report, then reconnect
            log(f"NFC reader: {error}; retrying")
            time.sleep(RETRY_DELAY)


def write(identifier):
    pn532 = connect()
    log("Hold a tag on the coil...")
    while not (uid := pn532.read_passive_target(timeout=0.5)):
        pass
    log(f"Written and verified: {write_tag_id(pn532, bytes(uid), identifier)}")


def main(argv):
    try:
        if not argv:
            run()
        elif argv == ["read"]:
            run(type_ids=False)
        elif len(argv) == 2 and argv[0] == "write":
            write(argv[1])
        else:
            raise SystemExit(__doc__)
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv[1:])
