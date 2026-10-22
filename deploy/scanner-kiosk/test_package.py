import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("station_builder", ROOT / "scripts/build-station.py")
validator = load("station_validator", Path(__file__).with_name("validate-package.py"))


class StationPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.source, self.output = self.folder / "dist", self.folder / "station-ui"
        (self.source / "assets").mkdir(parents=True)
        (self.source / "assets/app.js").write_text("window.applicationLoaded = true;")
        (self.source / "assets/app.css").write_text("body { margin: 0; }")
        (self.source / "index.html").write_text('<!doctype html><html><head><script type="module" src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css"></head><body><div id="root"></div></body></html>')

    def tearDown(self):
        self.temporary.cleanup()

    def test_live_package_bootstrap_precedes_react_and_includes_local_runtime(self):
        manifest = builder.build(self.source, self.output, "ppe-door")
        html = (self.output / "index.html").read_text()
        self.assertEqual(html, (self.output / "station.html").read_text())
        self.assertLess(html.index("station-config.js"), html.index("station-bootstrap.js"))
        self.assertLess(html.index("station-bootstrap.js"), html.index('type="module"'))
        self.assertIn('src="/station-runtime.js"', html)
        self.assertTrue((self.output / "assets/app.js").exists())
        self.assertIn('"fixed": true', (self.output / "station-config.js").read_text())
        self.assertIn('"roomName": "PPE conditioning room"', (self.output / "station-config.js").read_text())
        self.assertEqual(manifest["target_display"], {"width": 1280, "height": 720})
        self.assertFalse(manifest["offline_transactions"])
        self.assertEqual(validator.validate(self.output, "http://macbook-neo.local:8120", "192.168.1.1"), "ppe-door")

    def test_read_only_installer_check_accepts_fixed_station_override(self):
        builder.build(self.source, self.output)
        before = {str(path): path.read_bytes() for path in self.output.rglob("*") if path.is_file()}
        result = subprocess.run(["sh", str(Path(__file__).with_name("install.sh")), "--check",
                                 str(self.output), "http://192.168.1.50:8120", "kiosk", "192.168.1.1", "footwear-door"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("footwear-door", result.stdout)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.output.rglob("*") if path.is_file()})

    def test_read_only_installer_check_accepts_nfc_option(self):
        builder.build(self.source, self.output)
        result = subprocess.run(["sh", str(Path(__file__).with_name("install.sh")), "--check", "--nfc",
                                 str(self.output), "http://192.168.1.50:8120", "kiosk", "192.168.1.1"],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("shared-door", result.stdout)

    def test_changed_or_missing_assets_fail_before_installation(self):
        builder.build(self.source, self.output)
        (self.output / "assets/app.js").write_text("changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            validator.validate(self.output, "http://host:8120", "192.168.1.1")

    def test_mock_prototype_and_external_assets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Build the live"):
            validator.validate(self.source, "http://host:8120", "192.168.1.1")
        (self.source / "index.html").write_text('<html><script type="module" src="https://cdn.invalid/app.js"></script><div id="root"></div></html>')
        with self.assertRaisesRegex(ValueError, "local"):
            builder.build(self.source, self.output)

    def test_update_replaces_only_a_marked_station_output(self):
        self.output.mkdir()
        (self.output / "unrelated.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "Refusing"):
            builder.build(self.source, self.output)
        self.assertEqual((self.output / "unrelated.txt").read_text(), "keep")
        (self.output / "unrelated.txt").unlink()
        self.output.rmdir()
        builder.build(self.source, self.output)
        (self.source / "assets/app.js").write_text("window.applicationLoaded = 'updated';")
        builder.build(self.source, self.output)
        self.assertIn("updated", (self.output / "assets/app.js").read_text())

    def test_invalid_host_dns_station_and_package_paths_are_rejected(self):
        builder.build(self.source, self.output)
        for host, resolver, station in [("http://host:99999", "192.168.1.1", "ppe-door"),
                                         ("http://host/path", "192.168.1.1", "ppe-door"),
                                         ("http://host", "::1", "ppe-door"),
                                         ("http://host", "192.168.1.1", "ppe door")]:
            with self.subTest(host=host, resolver=resolver, station=station):
                with self.assertRaises(ValueError):
                    validator.validate(self.output, host, resolver, station)
        manifest = json.loads((self.output / "station-package.json").read_text())
        manifest["files"]["../outside.txt"] = "invalid"
        (self.output / "station-package.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "asset path"):
            validator.validate(self.output, "http://host", "192.168.1.1")


if __name__ == "__main__":
    unittest.main()
