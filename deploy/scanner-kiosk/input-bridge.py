#!/usr/bin/env python3
"""Read the USB scanner (and optional NFC hat) directly and stream scans to the kiosk page.

Chromium only receives keyboard-wedge input while it has OS focus. This bridge instead
reads the scanner's evdev device with an exclusive grab, so scans arrive whatever window
has focus and never leak as keystrokes into other applications. Each scan is sent to the
kiosk page as a server-sent event (nginx proxies /station/input/events here); the page
applies the same validation, health gating, and movement rules as keyboard capture.

Scans go only to pages connected at that moment: nothing is queued or replayed. If this
service stops, the grab is released and the scanner types into Chromium again, which the
page's keyboard listener still handles.

    input-bridge.py          run the bridge (fabric-scanner-input.service)
    input-bridge.py list     list input devices, to find the scanner's stable path

Environment (EnvironmentFile /opt/fabric-scanner/input.env and scanner.env):
    SCANNER_DEVICE   evdev path, ideally /dev/input/by-id/...-event-kbd (default: auto-detect)
    SCANNER_MATCH    comma-separated device-name fragments for auto-detect
    NFC              1 to also read a PN532 NFC hat through nfc_tags.py
"""
import errno
import itertools
import json
import logging
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766
RETRY_DELAY = 2.0         # seconds between attempts to (re)open a missing device
MAX_GAP = 0.35            # seconds between keys; matches the page's keyboard capture
MAX_LENGTH = 132          # characters per frame; longer input is rejected
KEEPALIVE = 15.0          # seconds between SSE comments on an idle stream
DEFAULT_MATCH = "barcode,scanner,hidkeyboard,eyoyo"
REJECT_MESSAGE = "Unrecognized scanner input. Rescan the complete label."

log = logging.getLogger("input-bridge")


# --- key events to scans ----------------------------------------------------

def build_keymap(ecodes):
    """keycode -> (unshifted, shifted) for a US-layout keyboard-wedge scanner."""
    keymap = {}
    for ch in "abcdefghijklmnopqrstuvwxyz":
        keymap[getattr(ecodes, f"KEY_{ch.upper()}")] = (ch, ch.upper())
    for digit, shifted in zip("1234567890", "!@#$%^&*()"):
        keymap[getattr(ecodes, f"KEY_{digit}")] = (digit, shifted)
        keymap[getattr(ecodes, f"KEY_KP{digit}")] = (digit, digit)
    for name, pair in {"MINUS": ("-", "_"), "EQUAL": ("=", "+"), "SPACE": (" ", " "),
                       "DOT": (".", ">"), "COMMA": (",", "<"), "SLASH": ("/", "?"),
                       "SEMICOLON": (";", ":"), "APOSTROPHE": ("'", '"'),
                       "LEFTBRACE": ("[", "{"), "RIGHTBRACE": ("]", "}"),
                       "BACKSLASH": ("\\", "|"), "GRAVE": ("`", "~")}.items():
        keymap[getattr(ecodes, f"KEY_{name}")] = pair
    return keymap


class FrameDecoder:
    """Assemble key-down events into complete scans, with the page capture's rejection rules."""

    def __init__(self, ecodes):
        self.keymap = build_keymap(ecodes)
        self.shift_keys = {ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT}
        self.enter_keys = {ecodes.KEY_ENTER, ecodes.KEY_KPENTER}
        self.shift = False
        self._clear()

    def _clear(self):
        self.buffer, self.invalid, self.last = [], False, None

    def feed(self, code, value, at):
        """Return ("scan", text) or ("reject", message) when Enter ends a frame, else None."""
        if code in self.shift_keys:
            self.shift = value != 0           # 1 = down, 2 = held, 0 = up
            return None
        if value != 1:                        # key-down only
            return None
        if self.buffer and self.last is not None and at - self.last > MAX_GAP:
            self.invalid = True               # interrupted frame: reject it at its Enter
        self.last = at
        if code in self.enter_keys:
            text, invalid = "".join(self.buffer), self.invalid
            self._clear()
            if invalid:
                return ("reject", REJECT_MESSAGE)
            return ("scan", text) if text else None
        pair = self.keymap.get(code)
        if pair is None or len(self.buffer) >= MAX_LENGTH:
            self.invalid = True
        else:
            self.buffer.append(pair[self.shift])
        return None


# --- delivery to kiosk pages ------------------------------------------------

class Hub:
    """Device status plus live delivery to every connected page. Nothing is buffered for later."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.subscribers = set()
        self.ids = itertools.count(1)
        self.status = {"scanner": "starting", "scanner_name": "", "nfc": "off"}

    def _send(self, message):
        for subscriber in list(self.subscribers):
            try:
                subscriber.put_nowait(message)
            except queue.Full:                # a stalled page loses its stream, not other pages
                self.subscribers.discard(subscriber)
        return len(self.subscribers)

    def set_status(self, **changes):
        with self.lock:
            if all(self.status.get(key) == value for key, value in changes.items()):
                return
            self.status.update(changes)
            self._send({"kind": "status", **self.status})

    def publish(self, kind, source, **fields):
        message = {"kind": kind, "id": next(self.ids), "source": source, "at": self.clock(), **fields}
        with self.lock:
            delivered = self._send(message)
        if not delivered:
            log.warning("%s %s dropped: no kiosk page connected", source, kind)
        return delivered

    def subscribe(self):
        subscriber = queue.Queue(maxsize=100)
        with self.lock:
            self.subscribers.add(subscriber)
            snapshot = {"kind": "status", **self.status}
        return subscriber, snapshot

    def unsubscribe(self, subscriber):
        with self.lock:
            self.subscribers.discard(subscriber)


def handler_for(hub):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/status":
                with hub.lock:
                    body = json.dumps({**hub.status, "pages": len(hub.subscribers)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/events":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            subscriber, snapshot = hub.subscribe()
            try:
                self.wfile.write(b"retry: 1000\n\n")
                self._event(snapshot)
                while True:
                    try:
                        self._event(subscriber.get(timeout=KEEPALIVE))
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except OSError:
                pass                          # page closed or reloaded
            finally:
                hub.unsubscribe(subscriber)

        def _event(self, message):
            self.wfile.write(f"data: {json.dumps(message)}\n\n".encode())
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    return Handler


def serve(hub, port=PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(hub))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# --- readers ----------------------------------------------------------------

def find_scanner(evdev, match):
    """First input device whose name contains a match fragment and that can send Enter."""
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        try:
            name = device.name.lower()
            keys = device.capabilities().get(evdev.ecodes.EV_KEY, [])
            if any(fragment in name for fragment in match) and evdev.ecodes.KEY_ENTER in keys:
                return path
        finally:
            device.close()
    return None


def scanner_worker(hub, evdev, device_path=None, match=(), sleep=time.sleep):
    while True:
        path = device_path or find_scanner(evdev, match)
        if not path:
            hub.set_status(scanner="missing", scanner_name="")
            sleep(RETRY_DELAY)
            continue
        try:
            device = evdev.InputDevice(path)
            try:
                device.grab()                 # exclusive: keystrokes no longer reach Chromium
                hub.set_status(scanner="ok", scanner_name=device.name)
                log.info("Reading scanner %s (%s)", device.name, path)
                decoder = FrameDecoder(evdev.ecodes)
                for event in device.read_loop():
                    if event.type != evdev.ecodes.EV_KEY:
                        continue
                    result = decoder.feed(event.code, event.value, event.timestamp())
                    if result and result[0] == "scan":
                        hub.publish("scan", "barcode", code=result[1])
                    elif result:
                        hub.publish("reject", "barcode", message=result[1])
            finally:
                try:
                    device.ungrab()
                except OSError:
                    pass
                device.close()
        except OSError as error:
            gone = error.errno in (errno.ENODEV, errno.ENOENT)
            hub.set_status(scanner="missing" if gone else "error", scanner_name="")
            log.warning("Scanner %s: %s; retrying", path, error)
        sleep(RETRY_DELAY)


def nfc_worker(hub, nfc_tags, sleep=time.sleep):
    while True:
        try:
            pn532 = nfc_tags.connect()
            hub.set_status(nfc="ok")
            for uid, specimen_id, error in nfc_tags.taps(pn532):
                if error:
                    log.info("NFC %s ignored: %s", uid.hex(":"), error)
                    hub.publish("reject", "nfc", message="This NFC tag does not hold a specimen ID.")
                else:
                    hub.publish("scan", "nfc", code=specimen_id)
        except Exception as error:            # SPI/hat failure: report, then reconnect
            hub.set_status(nfc="error")
            log.warning("NFC reader: %s; retrying", error)
        sleep(RETRY_DELAY)


def list_devices(evdev):
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        print(f"{path:20}  {device.name}")
        device.close()
    print("\nStable paths (use one as SCANNER_DEVICE):  ls -l /dev/input/by-id/")


def main(argv):
    import evdev

    if argv == ["list"]:
        list_devices(evdev)
        return
    if argv:
        raise SystemExit(__doc__)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    hub = Hub()
    serve(hub)
    match = [part.strip().lower() for part in os.environ.get("SCANNER_MATCH", DEFAULT_MATCH).split(",") if part.strip()]
    threading.Thread(target=scanner_worker, daemon=True,
                     args=(hub, evdev, os.environ.get("SCANNER_DEVICE") or None, match)).start()
    if os.environ.get("NFC") == "1":
        import nfc_tags
        threading.Thread(target=nfc_worker, args=(hub, nfc_tags), daemon=True).start()
    log.info("Streaming scans on http://127.0.0.1:%d/events", PORT)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main(sys.argv[1:])
