#!/usr/bin/env python3
"""Read the USB scanner (and optional NFC hat) directly and stream scans to the kiosk page.

Chromium only receives keyboard-wedge input while it has OS focus. This bridge instead
reads the scanner's evdev device with an exclusive grab, so scans arrive whatever window
has focus and never leak as keystrokes into other applications. Each scan is sent to the
kiosk page as a server-sent event (nginx proxies /station/input/events here); the page
applies the same validation, health gating, and movement rules as keyboard capture.

Scans go only to one active kiosk page. Disconnected scans are never replayed.
A bounded live-delivery queue rejects delayed frames rather than applying an old movement. If this
service stops, the grab is released and the scanner types into Chromium again, which the
page's keyboard listener still handles.

    input-bridge.py          run the bridge (fabric-scanner-input.service)
    input-bridge.py list     list input devices, to find the scanner's stable path

Environment (EnvironmentFile /opt/fabric-scanner/input.env and scanner.env):
    SCANNER_DEVICE   evdev path, ideally /dev/input/by-id/...-event-kbd (default: auto-detect)
    SCANNER_MATCH    comma-separated device-name fragments for auto-detect
    ENABLE_NFC       true to also read a PN532 NFC hat through nfc_tags.py (legacy NFC=1 accepted)
"""
import errno
import itertools
import fcntl
import math
import struct
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import uuid
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8766
RETRY_DELAY = 2.0         # seconds between attempts to (re)open a missing device
MAX_GAP = 0.35            # seconds between keys; matches the page's keyboard capture
MAX_LENGTH = 132          # characters per frame; longer input is rejected
KEEPALIVE = 1.0           # seconds between SSE comments on an idle stream
FUTURE_TOLERANCE_MS = 100.0
# Linux UAPI: _IOW('E', 0xa0, int), using the generic ARM/ARM64 ioctl encoding.
# https://github.com/torvalds/linux/blob/master/include/uapi/linux/input.h
# https://github.com/torvalds/linux/blob/master/include/uapi/asm-generic/ioctl.h
EVIOCSCLOCKID = (1 << 30) | (struct.calcsize("i") << 16) | (ord("E") << 8) | 0xA0
MAX_AGE_MS = 3000.0      # stale live events must never become later movements
DEFAULT_MATCH = "barcode,scanner,eyoyo"
REJECT_MESSAGE = "Unrecognized scanner input. Rescan the complete label."

log = logging.getLogger("input-bridge")


LOCAL_IPS_INTERVAL = 15.0  # seconds between station address refreshes (debug display only)


def local_ips():
    """Non-loopback IPv4 addresses of the station, without requiring the central API."""
    try:
        output = subprocess.check_output(["hostname", "-I"], text=True, timeout=1, stderr=subprocess.DEVNULL).split()
    except (OSError, subprocess.SubprocessError):
        return []
    return [ip for ip in output if ip.count(".") == 3 and ":" not in ip and not ip.startswith("127.")]


def local_ips_worker(hub, read=local_ips, sleep=time.sleep):
    """Keep the subprocess off the /status calibration path. Address changes (a DHCP lease
    arriving after boot, a renewal) reach a connected page as an ordinary SSE status event."""
    while True:
        try:
            hub.set_status(local_ips=read())
        except Exception as error:            # a debug display must never stop the bridge
            log.warning("Station address refresh failed: %s", error)
        sleep(LOCAL_IPS_INTERVAL)


def env_flag(name, environment=os.environ, default="false"):
    return environment.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


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
        self.num_lock = getattr(ecodes, "KEY_NUMLOCK", 69)
        self.down_key = getattr(ecodes, "KEY_DOWN", 108)
        self.held_shift = set()
        self.completed_at = None
        self.terminated_at = None
        self._clear()

    def _clear(self):
        self.buffer, self.invalid, self.last, self.started = [], False, None, None
        self.invalid_reasons = []
        self.key_trace = []

    def feed(self, code, value, at):
        """Return ("scan", text) or ("reject", message) when Enter ends a frame, else None."""
        if code == self.num_lock:
            # Some Eyoyo keyboard-wedge modes toggle Num Lock around each scan.
            # It carries no payload character and must not poison the frame.
            return None
        if (code == self.down_key and self.started is None and self.terminated_at is not None
                and 0 <= at - self.terminated_at <= MAX_GAP):
            # This Eyoyo sends Enter, Down, then restores Num Lock. Enter already
            # closed the frame. Consume only this observed, time-bounded trailer;
            # Down inside a payload or outside the suffix window remains invalid.
            if value == 1 and env_flag("SCANNER_DEBUG"):
                log.info("USB trailer key=KEY_DOWN after_enter_ms=%.1f", (at - self.terminated_at) * 1000)
            return None
        if value == 1 and env_flag("SCANNER_TRACE_KEYS") and len(self.key_trace) < 150:
            self.key_trace.append([code, round(at * 1000, 1)])
        if code in self.shift_keys:
            if value:
                self.held_shift.add(code)
            else:
                self.held_shift.discard(code)
            return None
        if value != 1:                        # key-down only
            return None
        if self.started is None:
            self.started = at
        if self.last is not None and (at < self.last or at - self.last > MAX_GAP):
            self.invalid = True               # interrupted/out-of-order frame: reject at Enter
            self.invalid_reasons.append("key_gap_or_order")
        self.last = at
        if code in self.enter_keys:
            text, invalid = "".join(self.buffer), self.invalid
            if env_flag("SCANNER_DEBUG"):
                log.info("USB frame text=%s length=%s invalid=%s reasons=%s", json.dumps(text), len(text), invalid,
                         json.dumps(self.invalid_reasons))
            if self.key_trace:                # collected only when SCANNER_TRACE_KEYS is set
                first = self.key_trace[0][1]
                log.info("USB frame keys_ms=%s", json.dumps([[key, round(at_ms - first, 1)] for key, at_ms in self.key_trace]))
            self.completed_at = self.started
            self.terminated_at = at
            self._clear()
            if invalid:
                return ("reject", REJECT_MESSAGE)
            return ("scan", text) if text else None
        pair = self.keymap.get(code)
        if pair is None or len(self.buffer) >= MAX_LENGTH:
            self.invalid = True
            self.invalid_reasons.append("unknown_key_" + str(code) if pair is None else "too_long")
        else:
            self.buffer.append(pair[bool(self.held_shift)])
        return None


# --- delivery to kiosk pages ------------------------------------------------

class Stream(queue.Queue):
    """A live stream has an explicit terminal event when its bounded queue overflows."""

    def __init__(self):
        super().__init__(maxsize=100)
        self.closed = False

    def close(self, message):
        # Called while Hub.lock is held. Discard queued scans: none may follow a terminal event.
        with self.mutex:
            self.closed = True
            self.queue.clear()
            self.queue.append(message)
            self.unfinished_tasks = 1
            self.not_empty.notify_all()


class Hub:
    """One active consumer, bounded live delivery, and process-scoped monotonic timestamps."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.session_id = str(uuid.uuid4())
        self.lock = threading.Lock()
        self.subscriber = None
        self.ids = itertools.count(1)
        self.status = {"scanner": "starting", "scanner_name": "", "scanner_error": "",
                       "nfc": "off", "nfc_error": "", "local_ips": []}

    def now_ms(self):
        return self.clock() * 1000.0

    def snapshot(self):
        # The caller holds lock when publishing or reading mutable status.
        return {"session_id": self.session_id, "now_ms": self.now_ms(), **self.status}

    def _send(self, message):
        subscriber = self.subscriber
        if subscriber is None or subscriber.closed:
            return 0
        try:
            subscriber.put_nowait(message)
        except queue.Full:
            subscriber.close({"kind": "consumer_overflow", "session_id": self.session_id,
                              "now_ms": self.now_ms(),
                              "message": "Scanner input delivery stalled. Reconnect and rescan the label."})
            log.warning("Scanner stream overflowed; disconnecting stalled page")
            return 0
        return 1

    def set_status(self, **changes):
        with self.lock:
            if all(self.status.get(key) == value for key, value in changes.items()):
                return
            self.status.update(changes)
            self._send({"kind": "status", **self.snapshot()})

    def publish(self, kind, source, **fields):
        with self.lock:
            message = {"kind": kind, "id": next(self.ids), "source": source,
                       "session_id": self.session_id, "at_ms": self.now_ms(), **fields}
            delivered = self._send(message)
        if not delivered:
            log.warning("%s %s dropped: no available kiosk page", source, kind)
        if env_flag("SCANNER_DEBUG"):
            log.info("input kind=%s source=%s delivered=%s fields=%s", kind, source, delivered,
                     json.dumps(fields, ensure_ascii=True))
        return delivered

    def subscribe(self):
        with self.lock:
            if self.subscriber is not None:
                return None, None
            subscriber = Stream()
            self.subscriber = subscriber
            return subscriber, {"kind": "status", **self.snapshot()}

    def unsubscribe(self, subscriber):
        with self.lock:
            if self.subscriber is subscriber:
                self.subscriber = None

    def receive(self, subscriber, timeout=KEEPALIVE):
        message = subscriber.get(timeout=timeout)
        if message["kind"] in ("scan", "reject"):
            age = self.now_ms() - message["at_ms"]
            if age < 0 or age > MAX_AGE_MS:
                return {"kind": "delivery_error", "id": message["id"],
                        "session_id": self.session_id, "at_ms": self.now_ms(),
                        "message": "Scanner input was delayed. Rescan the complete label."}
        return message


def handler_for(hub):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/status":
                with hub.lock:
                    body = json.dumps({**hub.snapshot(), "pages": int(hub.subscriber is not None)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/events":
                self.send_error(404)
                return
            subscriber, snapshot = hub.subscribe()
            if subscriber is None:
                self.send_error(409, "Scanner input is already active in another page")
                return
            try:
                self.connection.settimeout(5)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"retry: 1000\n\n")
                self._event(snapshot)
                while True:
                    try:
                        message = hub.receive(subscriber)
                        self._event(message)
                        if message["kind"] == "consumer_overflow":
                            break
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
            except OSError:
                pass                          # page closed or reloaded
            finally:
                self.close_connection = True
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
    server.worker = threading.Thread(target=server.serve_forever, name="http", daemon=True)
    server.worker.start()
    return server


# --- readers ----------------------------------------------------------------

def find_scanner(evdev, match):
    """Find a scanner despite hot-unplug/permission races involving unrelated input devices."""
    matches = []
    for path in evdev.list_devices():
        device = None
        try:
            device = evdev.InputDevice(path)
            name = device.name.lower()
            keys = device.capabilities().get(evdev.ecodes.EV_KEY, [])
            if any(fragment in name for fragment in match) and evdev.ecodes.KEY_ENTER in keys:
                matches.append(path)
        except OSError as error:
            log.info("Skipping unavailable input device %s: %s", path, error)
        finally:
            if device is not None:
                try:
                    device.close()
                except OSError:
                    pass
    if len(matches) > 1:
        raise RuntimeError("Multiple scanner devices match; set SCANNER_DEVICE to one stable by-id path")
    return matches[0] if matches else None


def configure_input_clock(device, ioctl=fcntl.ioctl):
    """Request kernel CLOCK_MONOTONIC timestamps; unsupported devices fail closed visibly."""
    try:
        ioctl(device.fd, EVIOCSCLOCKID, struct.pack("i", time.CLOCK_MONOTONIC))
    except OSError as error:
        raise RuntimeError(f"Cannot configure monotonic USB scanner timestamps: {error}") from error


def scanner_worker(hub, evdev, device_path=None, match=(), sleep=time.sleep,
                   clock=time.monotonic, configure_clock=configure_input_clock):
    while True:
        device, path = None, device_path
        try:
            path = device_path or find_scanner(evdev, match)
            if not path:
                hub.set_status(scanner="missing", scanner_name="", scanner_error="No matching USB scanner found")
            else:
                device = evdev.InputDevice(path)
                configure_clock(device)
                device.grab()                 # exclusive: keystrokes no longer reach Chromium
                hub.set_status(scanner="ok", scanner_name=device.name, scanner_error="")
                log.info("Reading scanner %s (%s)", device.name, path)
                decoder = FrameDecoder(evdev.ecodes)
                for event in device.read_loop():
                    if event.type == getattr(evdev.ecodes, "EV_SYN", 0) and event.code == getattr(evdev.ecodes, "SYN_DROPPED", 3):
                        decoder.invalid = True  # lost keys may otherwise form a different valid decimal ID
                        decoder.invalid_reasons.append("kernel_events_lost")
                        continue
                    if event.type != evdev.ecodes.EV_KEY:
                        continue
                    at = event.timestamp()  # original kernel monotonic time, including buffered events
                    age_ms = (clock() - at) * 1000.0
                    # A stale key-up after Enter must not poison the next fresh frame.
                    if event.value == 1 and (not math.isfinite(at) or age_ms > MAX_AGE_MS or age_ms < -FUTURE_TOLERANCE_MS):
                        decoder.invalid = True
                        decoder.invalid_reasons.append("event_age_ms_" + str(round(age_ms)) if math.isfinite(age_ms) else "nonfinite_event_time")
                    result = decoder.feed(event.code, event.value, at)
                    if result and result[0] == "scan":
                        hub.publish("scan", "barcode", code=result[1], at_ms=decoder.completed_at * 1000.0)
                    elif result:
                        hub.publish("reject", "barcode", message=result[1])
                hub.set_status(scanner="missing", scanner_name="", scanner_error="USB scanner reader stopped; reconnecting")
        except Exception as error:
            gone = isinstance(error, OSError) and error.errno in (errno.ENODEV, errno.ENOENT)
            hub.set_status(scanner="missing" if gone else "error", scanner_name="", scanner_error=str(error))
            log.warning("Scanner %s: %s; retrying", path or "discovery", error)
        finally:
            if device is not None:
                try:
                    device.ungrab()
                except OSError:
                    pass
                try:
                    device.close()
                except OSError:
                    pass
        sleep(RETRY_DELAY)


def nfc_worker(hub, nfc_tags, sleep=time.sleep):
    while True:
        pn532 = None
        try:
            pn532 = nfc_tags.connect()
            hub.set_status(nfc="ok", nfc_error="")
            for uid, specimen_id, error in nfc_tags.taps(pn532):
                if error:
                    log.info("NFC %s ignored: %s", uid.hex(":"), error)
                    hub.publish("reject", "nfc", message="This NFC tag does not hold a specimen ID.")
                else:
                    hub.publish("scan", "nfc", code=specimen_id)
            hub.set_status(nfc="error", nfc_error="NFC reader stopped; reconnecting")
        except Exception as error:            # SPI/hat failure: report, then reconnect
            hub.set_status(nfc="error", nfc_error=str(error))
            log.warning("NFC reader: %s; retrying", error)
        finally:
            if pn532 is not None and callable(getattr(nfc_tags, "disconnect", None)):
                nfc_tags.disconnect(pn532)
        sleep(RETRY_DELAY)


def list_devices(evdev):
    for path in evdev.list_devices():
        device = evdev.InputDevice(path)
        print(f"{path:20}  {device.name}")
        device.close()
    print("\nStable paths (use one as SCANNER_DEVICE):  ls -l /dev/input/by-id/")


def nfc_enabled(environment):
    return env_flag("ENABLE_NFC", environment, environment.get("NFC", "false"))


def main(argv):
    import evdev

    if argv == ["list"]:
        list_devices(evdev)
        return
    if argv:
        raise SystemExit(__doc__)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    hub = Hub()
    server = serve(hub)
    threading.Thread(target=local_ips_worker, args=(hub,), name="local-ips", daemon=True).start()
    match = [part.strip().lower() for part in os.environ.get("SCANNER_MATCH", DEFAULT_MATCH).split(",") if part.strip()]
    workers = [("scanner", scanner_worker, (hub, evdev, os.environ.get("SCANNER_DEVICE") or None, match))]
    if nfc_enabled(os.environ):
        import nfc_tags
        hub.set_status(nfc="starting")
        workers.append(("nfc", nfc_worker, (hub, nfc_tags)))
    threads = [server.worker]
    for name, target, args in workers:
        worker = threading.Thread(target=target, args=args, name=name, daemon=True)
        worker.start()
        threads.append(worker)
    log.info("Streaming scans on http://127.0.0.1:%d/events", PORT)
    while True:
        time.sleep(1)
        # Unexpected worker death must restart the whole service, releasing any exclusive grabs.
        dead = [worker.name for worker in threads if not worker.is_alive()]
        if dead:
            log.error("Input reader stopped unexpectedly: %s", ", ".join(dead))
            raise SystemExit(1)



if __name__ == "__main__":
    main(sys.argv[1:])
