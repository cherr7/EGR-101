import errno
import importlib.util
import json
import queue
from pathlib import Path
import types
import threading
import struct
from unittest.mock import Mock, patch
import unittest
import urllib.request
import urllib.error


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
    def test_recorded_eyoyo_hardware_sequence_preserves_all_eight_payloads(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures' / 'eyoyo-hid-scans.json').read_text())
        self.assertEqual(len(fixture['expected']), 8)
        self.assertEqual(decode(fixture['events']), [('scan', value) for value in fixture['expected']])

    def test_eyoyo_enter_down_numlock_trailer_does_not_poison_next_scan(self):
        events = []
        for start in (0, 3, 13):
            events += [(69, 1, start), (69, 0, start + .001)] + keys(PAYLOAD, start=start + .002, step=.006)
            end = start + .002 + len(PAYLOAD) * .006
            events += [(108, 1, end + .006), (108, 0, end + .007), (69, 1, end + .012), (69, 0, end + .013)]
        self.assertEqual(decode(events), [("scan", PAYLOAD)] * 3)

    def test_down_arrow_inside_payload_or_after_suffix_window_is_rejected(self):
        middle = keys(PAYLOAD[:10], enter='KEY_0')[:-2] + [(108, 1, .105)] + keys(PAYLOAD[10:], start=.11)
        self.assertEqual(decode(middle), [("reject", bridge.REJECT_MESSAGE)])
        events = keys(PAYLOAD) + [(108, 1, 1)] + keys(PAYLOAD, start=2)
        self.assertEqual(decode(events), [("scan", PAYLOAD), ("reject", bridge.REJECT_MESSAGE)])

    def test_scanner_num_lock_toggle_does_not_reject_or_poison_scans(self):
        events = [(69, 1, 0), (69, 0, .001)] + keys(PAYLOAD, start=.01)
        events += [(69, 1, .5), (69, 0, .501)] + keys(PAYLOAD, start=1)
        self.assertEqual(decode(events), [("scan", PAYLOAD), ("scan", PAYLOAD)])

    def test_rejected_frame_debug_log_includes_received_text_and_cause(self):
        events = [(ecodes.KEY_LEFTCTRL, 1, 0)] + keys(PAYLOAD, start=.01)
        with patch.dict(bridge.os.environ, {"SCANNER_DEBUG": "true"}), self.assertLogs(bridge.log, level="INFO") as captured:
            self.assertEqual(decode(events), [("reject", bridge.REJECT_MESSAGE)])
        self.assertIn('text="' + PAYLOAD + '"', captured.output[0])
        self.assertIn('unknown_key_' + str(ecodes.KEY_LEFTCTRL), captured.output[0])

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

    def test_releasing_one_shift_does_not_clear_the_other_shift(self):
        events = [(ecodes.KEY_LEFTSHIFT, 1, 0), (ecodes.KEY_RIGHTSHIFT, 1, .01),
                  (ecodes.KEY_LEFTSHIFT, 0, .02), (ecodes.KEY_A, 1, .03),
                  (ecodes.KEY_RIGHTSHIFT, 0, .04), (ecodes.KEY_ENTER, 1, .05)]
        self.assertEqual(decode(events), [("scan", "A")])

    def test_bare_enter_and_autorepeat_produce_nothing(self):
        self.assertEqual(decode([(ecodes.KEY_ENTER, 1, 0.0), (ecodes.KEY_1, 2, 0.1), (ecodes.KEY_ENTER, 1, 0.2)]), [])


class HubTests(unittest.TestCase):
    def test_debug_log_preserves_exact_scan_string_and_delivery(self):
        hub = bridge.Hub()
        hub.subscribe()
        code = '000000000000000000000000000000000000123'
        with patch.dict(bridge.os.environ, {"SCANNER_DEBUG": "true"}), self.assertLogs(bridge.log, level="INFO") as captured:
            hub.publish("scan", "barcode", code=code)
        self.assertIn('"code": "' + code + '"', captured.output[0])
        self.assertIn('delivered=1', captured.output[0])

    def test_scans_reach_connected_pages_only_and_are_never_replayed(self):
        hub = bridge.Hub(clock=lambda: 1000.0)
        self.assertEqual(hub.publish("scan", "barcode", code=PAYLOAD), 0)   # no page: dropped
        page, snapshot = hub.subscribe()
        self.assertEqual(snapshot["kind"], "status")
        self.assertTrue(page.empty())
        self.assertEqual(hub.publish("scan", "nfc", code=PAYLOAD), 1)
        message = page.get_nowait()
        self.assertEqual(message, {"kind": "scan", "id": 2, "source": "nfc", "session_id": hub.session_id,
                                   "at_ms": 1000000.0, "code": PAYLOAD})
        hub.set_status(scanner="ok", scanner_name="Eyoyo")
        hub.set_status(scanner="ok", scanner_name="Eyoyo")                  # unchanged: not resent
        self.assertEqual(page.get_nowait()["scanner"], "ok")
        self.assertTrue(page.empty())

    def test_first_active_page_owns_input_and_second_page_is_rejected(self):
        hub = bridge.Hub()
        first, _ = hub.subscribe()
        second, snapshot = hub.subscribe()
        self.assertIsNone(second)
        self.assertIsNone(snapshot)
        hub.publish("scan", "barcode", code=PAYLOAD)
        self.assertEqual(first.get_nowait()["code"], PAYLOAD)
        hub.unsubscribe(first)
        third, _ = hub.subscribe()
        self.assertIsNotNone(third)
        self.assertTrue(third.empty())  # the old movement is never replayed

    def test_overflow_closes_stream_and_never_replays_queued_scans(self):
        hub = bridge.Hub()
        page, _ = hub.subscribe()
        for _ in range(page.maxsize):
            self.assertEqual(hub.publish("scan", "barcode", code=PAYLOAD), 1)
        self.assertEqual(hub.publish("scan", "barcode", code=PAYLOAD), 0)
        self.assertTrue(page.closed)
        self.assertEqual(hub.receive(page)["kind"], "consumer_overflow")
        self.assertTrue(page.empty())
        self.assertEqual(hub.publish("scan", "barcode", code=PAYLOAD), 0)
        self.assertEqual(hub.subscribe(), (None, None))  # keep ownership until the handler closes
        hub.unsubscribe(page)
        new_page, _ = hub.subscribe()
        self.assertTrue(new_page.empty())
        hub.unsubscribe(page)  # finishing an old handler cannot remove the new owner
        self.assertIs(hub.subscriber, new_page)

    def test_stale_live_delivery_becomes_error_instead_of_scan(self):
        now = [1.0]
        hub = bridge.Hub(clock=lambda: now[0])
        page, snapshot = hub.subscribe()
        hub.publish("scan", "barcode", code=PAYLOAD)
        now[0] += bridge.MAX_AGE_MS / 1000 + .001
        message = hub.receive(page)
        self.assertEqual(message["kind"], "delivery_error")
        self.assertNotIn("code", message)
        self.assertEqual(message["id"], 1)
        self.assertEqual(message["session_id"], snapshot["session_id"])

    def test_process_session_and_monotonic_clock_not_wall_clock(self):
        first = bridge.Hub(clock=lambda: 12.5)
        second = bridge.Hub(clock=lambda: 12.5)
        self.assertNotEqual(first.session_id, second.session_id)
        page, snapshot = first.subscribe()
        self.assertEqual(snapshot["now_ms"], 12500)
        first.publish("scan", "barcode", code=PAYLOAD)
        self.assertEqual(page.get_nowait()["at_ms"], 12500)

    def test_overflow_terminates_actual_http_stream_and_allows_reconnect(self):
        hub = bridge.Hub()
        entered, release = threading.Event(), threading.Event()
        original_receive = hub.receive
        def delayed_receive(subscriber, timeout=bridge.KEEPALIVE):
            entered.set()
            if not release.wait(timeout=5):
                raise queue.Empty
            return original_receive(subscriber, timeout)
        hub.receive = delayed_receive
        server = bridge.serve(hub, port=0)
        stream = None
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            stream = urllib.request.urlopen(base + "/events", timeout=5)
            self.assertEqual(stream.readline(), b"retry: 1000\n")
            stream.readline()
            self.assertEqual(json.loads(stream.readline()[6:])["kind"], "status")
            stream.readline()
            self.assertTrue(entered.wait(timeout=2))
            for _ in range(101):
                hub.publish("scan", "barcode", code=PAYLOAD)
            release.set()
            self.assertEqual(json.loads(stream.readline()[6:])["kind"], "consumer_overflow")
            self.assertEqual(stream.readline(), b"\n")
            self.assertEqual(stream.readline(), b"")  # not an apparently-live keepalive-only stream
            with urllib.request.urlopen(base + "/status", timeout=5) as response:
                self.assertEqual(json.load(response)["pages"], 0)
            with urllib.request.urlopen(base + "/events", timeout=5) as replacement:
                self.assertEqual(replacement.readline(), b"retry: 1000\n")
        finally:
            release.set()
            if stream:
                stream.close()
            server.shutdown()
            server.server_close()

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
            with self.assertRaises(urllib.error.HTTPError) as second:
                urllib.request.urlopen(base + "/events", timeout=5)
            self.assertEqual(second.exception.code, 409)
            hub.publish("scan", "barcode", code=PAYLOAD)
            self.assertEqual(json.loads(stream.readline()[6:])["code"], PAYLOAD)
            with urllib.request.urlopen(base + "/status", timeout=5) as response:
                status = json.load(response)
                self.assertEqual(status["pages"], 1)
                self.assertEqual(status["session_id"], hub.session_id)
                self.assertGreater(status["now_ms"], 0)
                self.assertEqual(status["scanner"], "starting")
                self.assertIn("scanner_error", status)
                self.assertEqual(status["local_ips"], [])
            stream.close()
        finally:
            server.shutdown()
            server.server_close()


class FakeDevice:
    def __init__(self, path, name, events=(), fail=None, has_enter=True):
        self.path, self.name, self.events, self.fail, self.has_enter = path, name, events, fail, has_enter
        self.fd = 42  # Used only with an injected/mock ioctl, never a real device.
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
            bridge.scanner_worker(hub, self.fake_evdev(devices), match=["barcode"], sleep=stop, clock=lambda: .5, configure_clock=lambda device: None)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        self.assertEqual([m["code"] for m in messages if m["kind"] == "scan"], [PAYLOAD])
        self.assertEqual(statuses[0], {"scanner": "ok", "scanner_name": "Eyoyo Barcode Scanner", "scanner_error": ""})
        self.assertEqual(statuses[-1]["scanner"], "missing")
        self.assertTrue(statuses[-1]["scanner_error"])
        self.assertFalse(scanner.grabbed)

    def test_monotonic_clock_ioctl_matches_linux_uapi(self):
        ioctl = Mock()
        device = types.SimpleNamespace(fd=42)
        bridge.configure_input_clock(device, ioctl=ioctl)
        self.assertEqual(bridge.EVIOCSCLOCKID, 0x400445A0)
        ioctl.assert_called_once_with(42, 0x400445A0, struct.pack("i", bridge.time.CLOCK_MONOTONIC))

    def test_unsupported_kernel_clock_reports_error_without_grabbing_or_reading(self):
        device = FakeDevice("/scanner", "Eyoyo", keys(PAYLOAD))
        hub = bridge.Hub()
        page, _ = hub.subscribe()
        def unsupported(device):
            bridge.configure_input_clock(device, ioctl=Mock(side_effect=OSError(errno.EINVAL, "unsupported")))
        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({"/scanner": device}), device_path="/scanner",
                                  sleep=stop, configure_clock=unsupported)
        self.assertFalse(device.grabbed)
        self.assertEqual(hub.status["scanner"], "error")
        self.assertIn("monotonic", hub.status["scanner_error"])
        self.assertFalse(any(page.get_nowait()["kind"] == "scan" for _ in range(page.qsize())))

    def test_old_kernel_buffer_is_rejected_then_fresh_frame_keeps_original_timestamp(self):
        old = keys(PAYLOAD, start=1.0)
        fresh = keys(PAYLOAD, start=9.5)
        device = FakeDevice("/scanner", "Eyoyo", old + fresh)
        hub = bridge.Hub(clock=lambda: 10.0)
        page, _ = hub.subscribe()
        configured = []
        def configure(device):
            self.assertFalse(device.grabbed)
            configured.append(device)
        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({"/scanner": device}), device_path="/scanner",
                                  sleep=stop, clock=lambda: 10.0, configure_clock=configure)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        scans = [message for message in messages if message["kind"] == "scan"]
        self.assertEqual([message["code"] for message in scans], [PAYLOAD])
        self.assertEqual(scans[0]["at_ms"], 9500.0)  # not re-dated as 10000 at read time
        self.assertEqual(len([message for message in messages if message["kind"] == "reject"]), 1)
        self.assertEqual(configured, [device])

    def test_future_kernel_frame_and_original_inter_key_gap_are_rejected(self):
        events = keys(PAYLOAD, start=20.0)
        events += keys(PAYLOAD[:10], start=9.0)[:-2] + keys(PAYLOAD[10:], start=9.5)
        device = FakeDevice("/scanner", "Eyoyo", events)
        hub = bridge.Hub(clock=lambda: 10.0)
        page, _ = hub.subscribe()
        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({"/scanner": device}), device_path="/scanner",
                                  sleep=stop, clock=lambda: 10.0, configure_clock=lambda device: None)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        self.assertEqual([message["kind"] for message in messages if message["kind"] != "status"], ["reject", "reject"])

    def test_disappearing_unrelated_device_does_not_kill_discovery(self):
        scanner = FakeDevice("/scanner", "Eyoyo Barcode Scanner")
        def open_device(path):
            if path == "/gone":
                raise OSError(errno.ENODEV, "unplugged during discovery")
            return scanner
        evdev = types.SimpleNamespace(ecodes=ecodes, list_devices=lambda: ["/gone", "/scanner"],
                                      InputDevice=open_device)
        self.assertEqual(bridge.find_scanner(evdev, ["eyoyo"]), "/scanner")

    def test_multiple_matches_fail_without_grabbing_either(self):
        devices = {p: FakeDevice(p, "Eyoyo Barcode Scanner") for p in ("/one", "/two")}
        with self.assertRaisesRegex(RuntimeError, "SCANNER_DEVICE"):
            bridge.find_scanner(self.fake_evdev(devices), ["eyoyo"])
        self.assertFalse(any(device.grabbed for device in devices.values()))

    def test_discovery_error_is_visible_and_retried(self):
        attempts, statuses = [], []
        def fail_discovery():
            attempts.append(1)
            raise OSError(errno.EACCES, "permission denied")
        evdev = types.SimpleNamespace(ecodes=ecodes, list_devices=fail_discovery)
        hub = bridge.Hub()
        def retry(_seconds):
            statuses.append(dict(hub.status))
            if len(statuses) == 2:
                raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, evdev, match=["eyoyo"], sleep=retry, configure_clock=lambda device: None)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(status["scanner"] == "error" for status in statuses))
        self.assertIn("permission denied", statuses[0]["scanner_error"])

    def test_unexpected_reader_exception_releases_grab_and_reports_error(self):
        device = FakeDevice("/scanner", "Eyoyo")
        def broken_loop():
            raise ValueError("invalid input event")
        device.read_loop = broken_loop
        hub = bridge.Hub()
        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({"/scanner": device}), device_path="/scanner", sleep=stop, configure_clock=lambda device: None)
        self.assertFalse(device.grabbed)
        self.assertEqual(hub.status["scanner"], "error")
        self.assertIn("invalid input event", hub.status["scanner_error"])

    def test_missing_scanner_is_reported(self):
        hub = bridge.Hub()

        def stop(_seconds):
            raise StopWorker
        with self.assertRaises(StopWorker):
            bridge.scanner_worker(hub, self.fake_evdev({}), match=["barcode"], sleep=stop, clock=lambda: .5, configure_clock=lambda device: None)
        self.assertEqual(hub.status["scanner"], "missing")


class NfcWorkerTests(unittest.TestCase):
    def test_installer_setting_enables_nfc_and_explicit_false_overrides_legacy(self):
        self.assertTrue(bridge.nfc_enabled({"ENABLE_NFC": "true"}))
        self.assertTrue(bridge.nfc_enabled({"NFC": "1"}))
        self.assertFalse(bridge.nfc_enabled({"ENABLE_NFC": "false", "NFC": "1"}))
        self.assertFalse(bridge.nfc_enabled({}))

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
        self.assertEqual(hub.status["nfc"], "error")
        self.assertIn("stopped", hub.status["nfc_error"])


class LocalAddressTests(unittest.TestCase):
    def test_only_non_loopback_ipv4_addresses_are_reported(self):
        output = "192.168.1.50 127.0.1.1 10.0.0.7 fd12:3456::1 2001:db8::7\n"
        with patch.object(bridge.subprocess, "check_output", return_value=output):
            self.assertEqual(bridge.local_ips(), ["192.168.1.50", "10.0.0.7"])
        with patch.object(bridge.subprocess, "check_output", side_effect=bridge.subprocess.TimeoutExpired("hostname", 1)):
            self.assertEqual(bridge.local_ips(), [])

    def test_late_lease_reaches_status_and_live_stream_once(self):
        hub = bridge.Hub()
        page, snapshot = hub.subscribe()
        self.assertEqual(snapshot["local_ips"], [])
        # Boot before DHCP, an unexpected reader failure, the lease, then an unchanged refresh.
        answers = [[], ValueError("unreadable hostname output"), ["192.168.1.50"], ["192.168.1.50"]]

        def read():
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        def sleep(_seconds):
            if not answers:
                raise StopWorker
        with self.assertLogs(bridge.log, level="WARNING"), self.assertRaises(StopWorker):
            bridge.local_ips_worker(hub, read=read, sleep=sleep)
        messages = [page.get_nowait() for _ in range(page.qsize())]
        self.assertEqual([(m["kind"], m["local_ips"]) for m in messages], [("status", ["192.168.1.50"])])
        self.assertEqual(hub.status["local_ips"], ["192.168.1.50"])


if __name__ == "__main__":
    unittest.main()
