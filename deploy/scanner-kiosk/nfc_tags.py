#!/usr/bin/env python3
"""Read and write specimen IDs on NTAG21x tags with a PN532 NFC hat (SPI).

input-bridge.py imports this module when NFC is enabled: each tap's NDEF text record is
validated as a specimen ID and streamed to the kiosk page like a barcode scan. Tags that
hold anything else are reported as rejected scans, never as IDs.

    nfc_tags.py read          print each tapped tag's ID (stop fabric-scanner.target first)
    nfc_tags.py write CODE    write a specimen label code (as printed on its label) to one NTAG tag

Hat wiring: I1=H, I0=L, RSTPDN->D20, DIP 1-4 (SCK/MISO/MOSI/D4) ON, 5-8 (SCL/SDA/TX/RX) OFF.
"""
import sys
import time

POLL_TIMEOUT = 0.1    # seconds per detection attempt
REARM_AFTER = 0.75    # a tag must be off the coil this long before it reports again
RETRY_DELAY = 2.0     # seconds before reconnecting a hat that failed
MAX_READ_ERRORS = 10  # reconnect after persistent hardware failure rather than spinning forever


# --- specimen IDs -----------------------------------------------------------

def normalize_id(text):
    """Return a specimen label code (number + check digit, verified by the server), else raise ValueError."""
    value = str(text).strip()
    if not (value.isascii() and value.isdecimal() and 2 <= len(value) <= 20):
        raise ValueError(f"not a specimen label code: {value[:60]!r}")
    return value


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
    """Return the specimen label code stored on a tag, or raise ValueError/RuntimeError."""
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

    resources = []
    try:
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        resources.append(spi)
        chip_select = DigitalInOut(board.D4)
        resources.append(chip_select)
        reset = DigitalInOut(board.D20)
        resources.append(reset)
        pn532 = PN532_SPI(spi, chip_select, reset=reset)
        pn532._fabric_resources = resources
        _, version, revision, _ = pn532.firmware_version
        log(f"PN532 firmware {version}.{revision}")
        pn532.SAM_configuration()
        return pn532
    except BaseException:
        release_resources(resources)
        raise


def release_resources(resources):
    for resource in reversed(resources):
        try:
            resource.deinit()
        except Exception as error:
            log(f"NFC resource cleanup: {error}")


def disconnect(pn532):
    """Release GPIO/SPI before reconnecting; retaining them can leave the pins permanently busy."""
    resources = getattr(pn532, "_fabric_resources", [])
    pn532._fabric_resources = []
    release_resources(resources)


def log(message):
    print(message, flush=True)


def taps(pn532, tag_filter=None, clock=time.monotonic):
    """Yield (uid, specimen ID or None, error or None) once per tag tap."""
    tag_filter = tag_filter or TagFilter()
    failures = 0
    while True:
        try:
            uid = pn532.read_passive_target(timeout=POLL_TIMEOUT)
        except RuntimeError:
            failures += 1
            if failures >= MAX_READ_ERRORS:
                raise RuntimeError("NFC reader failed repeatedly; reconnecting") from None
            time.sleep(POLL_TIMEOUT)
            continue  # tolerate transient garbled frames at the edge of range
        failures = 0
        if not uid:
            continue
        uid = bytes(uid)
        if not tag_filter.is_new_tap(uid, clock()):
            continue
        try:
            yield uid, read_tag_id(pn532, uid), None
        except (ValueError, RuntimeError) as error:
            yield uid, None, str(error)


def watch():
    pn532 = connect()
    log("Waiting for NFC tags...")
    for uid, specimen_id, error in taps(pn532):
        log(f"{uid.hex(':')}  {specimen_id or 'ignored: ' + error}")


def write(identifier):
    pn532 = connect()
    log("Hold a tag on the coil...")
    while not (uid := pn532.read_passive_target(timeout=0.5)):
        pass
    log(f"Written and verified: {write_tag_id(pn532, bytes(uid), identifier)}")


def main(argv):
    try:
        if argv == ["read"]:
            watch()
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
