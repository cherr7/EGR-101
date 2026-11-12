"""Packaging checks use temporary files and never open a browser or register launchd."""
import os
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_app
import host


class NativePackagingTests(unittest.TestCase):
    def test_checkpoint_cli_exports_checked_portable_archive(self):
        archive = b"portable checkpoint archive"
        record = {"id": "a" * 32, "sha256": hashlib.sha256(archive).hexdigest()}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(host, "config", return_value={"http_port": 8120}), \
             patch.object(host, "urlopen", side_effect=[io.BytesIO(json.dumps(record).encode()), io.BytesIO(archive)]) as request:
            destination = Path(folder) / "checkpoint.zip"
            self.assertEqual(host.export_checkpoint(destination), destination.resolve())
            self.assertEqual(destination.read_bytes(), archive)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(request.call_args_list[0].args[0].method, "POST")

    def test_checkpoint_cli_rejects_corruption_without_partial_export(self):
        record = {"id": "a" * 32, "sha256": "0" * 64}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(host, "config", return_value={"http_port": 8120}), \
             patch.object(host, "urlopen", side_effect=[io.BytesIO(json.dumps(record).encode()), io.BytesIO(b"damaged")]):
            destination = Path(folder) / "checkpoint.zip"
            with self.assertRaisesRegex(RuntimeError, "checksum"):
                host.export_checkpoint(destination)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_checkpoint_cli_never_overwrites_existing_export(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(host, "urlopen") as request:
            destination = Path(folder) / "checkpoint.zip"
            destination.write_bytes(b"original")
            with self.assertRaisesRegex(RuntimeError, "overwrite"):
                host.export_checkpoint(destination)
            self.assertEqual(destination.read_bytes(), b"original")
            request.assert_not_called()

    def test_app_registers_a_nonroot_boot_service_and_holds_no_code_or_data(self):
        with tempfile.TemporaryDirectory() as folder:
            app = build_app.build(Path(folder) / "Fabric Conditioning.app")
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleIdentifier"], "org.egr.fabric-conditioning")
            self.assertTrue(info["LSUIElement"])
            for name in (info["CFBundleExecutable"], "fabric-service"):
                self.assertTrue((app / "Contents/MacOS" / name).stat().st_mode & 0o111)
            job = plistlib.loads((app / "Contents/Library/LaunchDaemons/org.egr.fabric-conditioning.plist").read_bytes())
            self.assertEqual(job["BundleProgram"], "Contents/MacOS/fabric-service")
            self.assertNotEqual(job["UserName"], "root")
            self.assertTrue(job["RunAtLoad"] and job["KeepAlive"])
            login = plistlib.loads((app / "Contents/Resources/login-service.plist").read_bytes())
            self.assertNotIn("UserName", login)
            self.assertTrue(login["ProgramArguments"][0].endswith("Contents/MacOS/fabric-service"))
            serve = (app / "Contents/Resources/serve.zsh").read_text()
            self.assertIn("releases/current", serve)
            self.assertIn("host.py\" serve", serve)
            self.assertFalse(list(app.rglob("config.json")))
            self.assertFalse(list(app.rglob("PG_VERSION")))
            self.assertFalse(list(app.rglob("main.py")))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        patcher = patch.dict(os.environ, {"FABRIC_STATE_DIR": self.folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = Path(self.folder.name)

    def test_hold_expires(self):
        self.assertFalse(host.held())
        (self.state / "hold").write_text(str(__import__("time").time()))
        self.assertTrue(host.held())
        (self.state / "hold").write_text(str(__import__("time").time() - host.HOLD_SECONDS - 1))
        self.assertFalse(host.held())

    def test_current_release_follows_the_link(self):
        releases = self.state / "releases"
        (releases / "v1").mkdir(parents=True)
        (releases / "v2").mkdir()
        self.assertIsNone(host.current_release())
        (releases / "current").symlink_to("v1")
        first = host.current_release()
        (releases / "current").unlink()
        (releases / "current").symlink_to("v2")
        self.assertNotEqual(first, host.current_release())

    def test_tunnel_is_off_without_a_passcode(self):
        (self.state / "outside.json").write_text(json.dumps({"enabled": True}))
        self.assertFalse(host.outside_enabled())
        (self.state / "outside.json").write_text(json.dumps({"enabled": True, "passcode_hash": "x"}))
        self.assertTrue(host.outside_enabled())

    def test_tunnel_link_is_read_from_the_log(self):
        (self.state / "logs").mkdir()
        (self.state / "outside.json").write_text(json.dumps({"enabled": True, "passcode_hash": "x"}))
        (self.state / "logs/tunnel.log").write_text("INF |  https://quiet-river-abc.trycloudflare.com  |\n")
        with patch.object(host, "tunnel_pid", return_value=4242):
            host.tunnel_tick()
        self.assertEqual(json.loads((self.state / "tunnel.json").read_text())["url"],
                         "https://quiet-river-abc.trycloudflare.com")


if __name__ == "__main__":
    unittest.main()
