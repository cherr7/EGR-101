import errno
import importlib.util
import json
from pathlib import Path
import types
import unittest
import urllib.request


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("input_bridge", Path(__file__).with_name("input-bridge.py"))

# Linux input-event-codes values, so tests run without python-evdev installed.
CODES = {"KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6, "KEY_6": 7, "KEY_7": 8, "KEY_8": 9,
         "KEY_9": 10, "KEY_0": 11, "KEY_MINUS": 12, "KEY_EQUAL": 13, "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18,
         "KEY_R": 19, "KEY_T": 20, "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
         "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_A": 30, "KEY_S": 31, "KEY_D": 32,
         "KEY_F": 33, "KEY_G": 34, "KEY_H": 35, "KEY_J": 36, "KEY_K": 37, "KEY_L": 38, "KEY_SEMICOLON": 39,
         "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41, "KEY_LEFTSHIFT": 42, "KEY_BACKSLASH": 43, "KEY_Z": 44,
         "KEY_X": 45, "KEY_C": 46, "KEY_V": 47, "KEY_B": 48, "KEY_N": 49, "KEY_M": 50, "KEY_COMMA": 51,
         "KEY_DOT": 52, "KEY_SLASH": 53, "KEY_RIGHTSHIFT": 54, "KEY_SPACE": 57, "KEY_KP7": 71, "KEY_KP8": 72,
         "KEY_KP9": 73, "KEY_KP4": 75, "KEY_KP5": 76, "KEY_KP6": 77, "KEY_KP1": 79, "KEY_KP2": 80,
         "KEY_KP3": 81, "KEY_KP0": 82, "KEY_KPENTER": 96, "KEY_LEFTCTRL": 29, "EV_KEY": 1, "EV_MSC": 4}
ecodes = types.SimpleNamespace(**CODES)
PAYLOAD = "000000000000000000000000000000000000042"


def keys(text, start=0.0, step=0.01, enter="KEY_ENTER"):
    """Key-down/up events for `text` then Enter, as (code, value, timestamp)."""
    events, at = [], start
    for ch in text:
        name = "KEY_" + ({"-": "MINUS", " ": "SPACE"}.get(ch) or ch.upper())
        shifted = ch.isupper()
        if shifted:
            events.append((ecodes.KEY_LEFTSHIFT, 1, at))
        events += [(getattr(ecodes, name), 1, at), (getattr(ecodes, name), 0, at)]
        if shifted:
            events.append((ecodes.KEY_LEFTSHIFT, 0, at))
        at += step
    return events + [(getattr(ecodes, enter), 1, at), (getattr(ecodes, enter), 0, at)]


def decode(events):
    decoder = bridge.FrameDecoder(ecodes)
    return [result for event in events if (result := decoder.feed(*event))]


class FrameDecoderTests(unittest.TestCase):
    def test_complete_scan_with_case_and_punctuation(self):
        self.assertEqual(decode(keys(PAYLOAD)), [("scan", PAYLOAD)])
        self.assertEqual(decode(keys("UL-Fab 01")), [("scan", "UL-Fab 01")])

    def test_keypad_digits_and_enter(self):
        events = [(getattr(ecodes, f"KEY_KP{d}"), 1, i * 0.01) for i, d in enumerate("90210")]
        events.append((ecodes.KEY_KPENTER, 1, 0.1))
        self.assertEqual(decode(events), [("scan", "90210")])

    def test_split_frame_is_rejected_then_next_scan_is_clean(self):
        events = keys(PAYLOAD[:15], enter="KEY_0")[:-2]
        events += [(c, v, t + 0.5) for c, v, t in keys(PAYLOAD[15:])]          # 500 ms pause mid-label
        events += [(c, v, t + 5) for c, v, t in keys(PAYLOAD)]
        self.assertEqual(decode(events), [("reject", bridge.REJECT_MESSAGE), ("scan", PAYLOAD)])

    def test_unmapped_and_oversized_frames_are_rejected(self):
        events = [(ecodes.KEY_LEFTCTRL, 1, 0.0)] + keys("123", start=0.01)
        self.assertEqual(decode(events), [("reject", bridge.REJECT_MESSAGE)])
        self.assertEqual(decode(keys("1" * 133, step=0.001)), [("reject", bridge.REJECT_MESSAGE)])
        self.assertEqual(decode(keys("1" * 132, step=0.001)), [("scan", "1" * 132)])

    def test_bare_enter_and_autorepeat_produce_nothing(self):
        self.assertEqual(decode([(ecodes.KEY_ENTER, 1, 0.0), (ecodes.KEY_1, 2, 0.1), (ecodes.KEY_ENTER, 1, 0.2)]), [])


class HubTests(unittest.TestCase):
    def test_scans_reach_connected_pages_only_and_are_never_replayed(self):
        hub = bridge.Hub(clock=lambda: 1000.0)
        self.assertEqual(hub.publish("scan", "barcode", code=PAYLOAD), 0)   # no page: dropped
        page, snapshot = hub.subscribe()
        self.assertEqual(snapshot["kind"], "status")
        self.assertTrue(page.empty())
        self.assertEqual(hub.publish("scan", "nfc", code=PAYLOAD), 1)
        self.assertEqual(page.get_nowait(), {"kind": "scan", "id": 2, "source": "nfc", "at": 1000.0, "code": PAYLOAD})
        hub.set_status(scanner="ok", scanner_name="Eyoyo")
        hub.set_status(scanner="ok", scanner_name="Eyoyo")                  # unchanged: not resent
        self.assertEqual(page.get_nowait()["scanner"], "ok")
        self.assertTrue(page.empty())

    def test_event_stream_over_http(self):
        hub = bridge.Hub()
        server = bridge.serve(hub, port=0)
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            stream = urllib.request.urlopen(base + "/events", timeout=5)
            self.assertEqual(stream.headers["Content-Type"], "text/event-stream")
            self.assertEqual(stream.readline(), b"retry: 1000\n")
            stream.readline()
            self.assertEqual(json.loads(stream.readline()[6:])["kind"], "status")
            stream.readline()
            hub.publish("scan", "barcode", code=PAYLOAD)
            self.assertEqual(json.loads(stream.readline()[6:])["code"], PAYLOAD)
            with urllib.request.urlopen(base + "/status", timeout=5) as response:
                self.assertEqual(json.load(response)["pages"], 1)
            stream.close()
        finally:
            server.shutdown()
            server.server_close()


class FakeDevice:
    def __init__(self, path, name, events=(), fail=None, has_enter=True):
        self.path, self.name, self.events, self.fail, self.has_enter = path, name, events, fail, has_enter
        self.grabbed = False

    def capabilities(self):
        return {ecodes.EV_KEY: [ecodes.KEY_ENTER] if self.has_enter else [ecodes.KEY_1]}

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        pass

    def read_loop(self):
        for code, value, at in self.events:
            yield types.SimpleNamespace(type=ecodes.EV_KEY, code=code, value=value, timestamp=lambda at=at: at)
        raise OSError(errno.ENODEV, "No such device")   # unplugged


class StopWorker(Exception):
    pass


class ScannerWorkerTests(unittest.TestCase):
    def fake_evdev(self, devices):
        return types.SimpleNamespace(ecodes=ecodes, list_devices=lambda: list(devices),
                                     InputDevice=lambda path: devices[path])

    def test_auto_detects_reads_and_reports_unplug(self):
        scanner = FakeDevice("/dev/input/event3", "Eyoyo Barcode Scanner", keys(PAYLOAD))
        devices = {"/dev/input/event1": FakeDevice("/dev/input/event1", "Logitech Keyboard"),
                   "/dev/input/event2": FakeDevice("/dev/input/event2", "Barcode Scanner Consumer Control", has_enter=False),
                   "/dev/input/event3": scanner}
        hub = bridge.Hub()
        page, _ = hub.subscribe()
        statuses = []
        original = hub.set_status
        hub.set_status = lambda **changes: (statuses.append(dict(changes)), original(**changes))

        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev(devices), match=["barcode"], sleep=stop)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        self.assertEqual([m["code"] for m in messages if m["kind"] == "scan"], [PAYLOAD])
        self.assertEqual(statuses[0], {"scanner": "ok", "scanner_name": "Eyoyo Barcode Scanner"})
        self.assertEqual(statuses[-1], {"scanner": "missing", "scanner_name": ""})
        self.assertFalse(scanner.grabbed)

    def test_missing_scanner_is_reported(self):
        hub = bridge.Hub()

        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({}), match=["barcode"], sleep=stop)
        self.assertEqual(hub.status["scanner"], "missing")


class NfcWorkerTests(unittest.TestCase):
    def test_tag_ids_and_bad_tags_become_scans_and_rejects(self):
        uid = bytes(range(7))
        nfc = types.SimpleNamespace(connect=lambda: object(),
                                    taps=lambda _pn532: iter([(uid, PAYLOAD, None), (uid, None, "tag holds no NDEF text record")]))
        hub = bridge.Hub()
        page, _ = hub.subscribe()

        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.nfc_worker(hub, nfc, sleep=stop)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        self.assertEqual([(m["kind"], m.get("code")) for m in messages if m["kind"] != "status"],
                         [("scan", PAYLOAD), ("reject", None)])
        self.assertEqual(hub.status["nfc"], "ok")


if __name__ == "__main__":
    unittest.main()
