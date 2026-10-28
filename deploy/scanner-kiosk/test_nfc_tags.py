import importlib.util
from pathlib import Path
import unittest
import types
from unittest.mock import patch


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bridge = load("nfc_tags", Path(__file__).with_name("nfc_tags.py"))
NTAG_UID = bytes(range(1, 8))
SPECIMEN = "123459"   # specimen 12345 + its check digit


class FakeTag:
    """NTAG213 memory: 4-byte pages, capability container on page 3."""

    def __init__(self, uid=NTAG_UID, formatted=True, taps=()):
        self.uid = uid
        self.pages = bytearray(45 * 4)
        if formatted:
            self.pages[12:16] = bytes([0xE1, 0x10, 0x12, 0x00])  # 144 bytes of user memory
            self.pages[16:20] = bytes([0x03, 0x00, 0xFE, 0x00])  # empty NDEF message
        self.taps = list(taps)

    def mifare_classic_read_block(self, page):
        return self.pages[page * 4:page * 4 + 16]

    def ntag2xx_write_block(self, page, data):
        self.pages[page * 4:page * 4 + 4] = data
        return True

    def read_passive_target(self, timeout):
        if not self.taps:
            raise StopIteration
        return self.taps.pop(0)


class IdentifierTests(unittest.TestCase):
    def test_label_codes_are_the_payload(self):
        self.assertEqual(bridge.normalize_id(" 123459\n"), SPECIMEN)
        self.assertEqual(bridge.normalize_id("92233720368547758077"), "92233720368547758077")

    def test_non_ids_are_rejected(self):
        for text in ["", "7", "UL-FAB-0001", "http://10.0.0.1/test", "1" * 21, "4d2a1c3b-0000-4000-8000-00000000002a", "١٢٣"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                bridge.normalize_id(text)


class TagTests(unittest.TestCase):
    def test_truncated_text_and_tlv_lengths_never_become_partial_identifiers(self):
        record = bridge.encode_ndef_text("1234567890")[2:-1]
        self.assertIsNone(bridge.decode_text_record(record[:8]))
        self.assertIsNone(bridge.decode_text_record(bytes([0xC1, 1, 0])))
        tag = FakeTag()
        tag.pages[16:20] = bytes([0x03, 0xFF, 0x02, 0x00])
        with self.assertRaisesRegex(ValueError, "capacity"):
            bridge.read_tag_id(tag, NTAG_UID)

    def test_short_hardware_reads_are_reported(self):
        tag = FakeTag()
        tag.mifare_classic_read_block = lambda page: b"\xe1"
        with self.assertRaisesRegex(RuntimeError, "read failed"):
            bridge.read_tag_id(tag, NTAG_UID)

    def test_written_id_reads_back_as_the_payload(self):
        tag = FakeTag()
        payload = bridge.write_tag_id(tag, tag.uid, SPECIMEN)
        self.assertEqual(payload, SPECIMEN)
        self.assertEqual(bridge.read_tag_id(tag, tag.uid), payload)

    def test_unusable_tags_are_reported_not_typed(self):
        with self.assertRaisesRegex(ValueError, "NTAG21x"):
            bridge.read_tag_id(FakeTag(), bytes(4))
        with self.assertRaisesRegex(ValueError, "NDEF-formatted"):
            bridge.read_tag_id(FakeTag(formatted=False), NTAG_UID)
        with self.assertRaisesRegex(ValueError, "no NDEF text"):
            bridge.read_tag_id(FakeTag(), NTAG_UID)
        tag = FakeTag()
        message = bridge.encode_ndef_text("UL-FAB-0001")
        tag.pages[16:16 + len(message)] = message
        with self.assertRaisesRegex(ValueError, "not a specimen label code"):
            bridge.read_tag_id(tag, NTAG_UID)
        with self.assertRaisesRegex(ValueError, "refusing"):
            bridge.write_tag_id(FakeTag(formatted=False), NTAG_UID, SPECIMEN)

    def test_tag_resting_on_coil_reports_once_until_lifted(self):
        tags = bridge.TagFilter(rearm_after=0.75)
        self.assertTrue(tags.is_new_tap(NTAG_UID, 0.0))
        self.assertFalse(tags.is_new_tap(NTAG_UID, 0.5))
        self.assertFalse(tags.is_new_tap(NTAG_UID, 1.0))   # still on the coil
        self.assertTrue(tags.is_new_tap(NTAG_UID, 2.0))    # lifted, then tapped again
        self.assertTrue(tags.is_new_tap(bytes(7), 2.1))    # a different tag

    def test_disconnect_releases_gpio_before_spi_and_is_idempotent(self):
        released = []
        resources = [types.SimpleNamespace(deinit=lambda name=name: released.append(name))
                     for name in ("spi", "chip_select", "reset")]
        reader = types.SimpleNamespace(_fabric_resources=resources)
        bridge.disconnect(reader)
        self.assertEqual(released, ["reset", "chip_select", "spi"])
        bridge.disconnect(reader)
        self.assertEqual(len(released), 3)

    def test_persistent_hardware_errors_escape_to_reader_reconnect(self):
        tag = FakeTag()
        calls = []
        def fail_read(timeout):
            calls.append(timeout)
            raise RuntimeError("SPI stopped")
        tag.read_passive_target = fail_read
        with patch.object(bridge.time, "sleep"), self.assertRaisesRegex(RuntimeError, "reconnecting"):
            next(bridge.taps(tag))
        self.assertEqual(len(calls), bridge.MAX_READ_ERRORS)

    def test_taps_yield_ids_and_errors_once_per_tap(self):
        tag = FakeTag(taps=[None, NTAG_UID, NTAG_UID, bytes(4)])
        payload = bridge.write_tag_id(tag, NTAG_UID, SPECIMEN)
        clock = iter([0.0, 0.1, 5.0]).__next__
        results = []
        with self.assertRaises(RuntimeError):  # StopIteration inside a generator
            for result in bridge.taps(tag, clock=clock):
                results.append(result)
        self.assertEqual(results[0], (NTAG_UID, payload, None))
        self.assertEqual(results[1][:2], (bytes(4), None))
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
