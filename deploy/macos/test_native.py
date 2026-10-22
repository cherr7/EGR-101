"""Packaging checks use temporary files and never open a browser or register launchd."""
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
import install_daemon
import launcher
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

    def test_bundle_contains_code_and_executable_without_private_state(self):
        with tempfile.TemporaryDirectory() as folder:
            app = build_app.build(Path(folder) / "Fabric Conditioning.app")
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info["CFBundleIdentifier"], "org.egr.fabric-conditioning")
            executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
            self.assertTrue(executable.stat().st_mode & 0o111)
            code = app / "Contents/Resources/app"
            self.assertTrue((code / "backend/app/main.py").is_file())
            self.assertTrue((code / "frontend/dist/index.html").is_file())
            self.assertFalse(list(app.rglob("config.json")))
            self.assertFalse(list(app.rglob("PG_VERSION")))
            self.assertFalse(list(app.rglob(".venv")))

    def test_check_does_not_launch_process_or_browser(self):
        with patch.object(sys, "argv", ["launcher", "--check"]), \
             patch.object(launcher, "APP_ROOT", build_app.ROOT), \
             patch.object(launcher, "runtime_path", return_value=Path(sys.executable)), \
             patch.object(launcher.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(launcher.main(), 0)
            self.assertTrue(json.loads(output.getvalue())["ready_to_launch"])
            run.assert_not_called()

    def test_healthy_no_open_does_not_start_duplicate_host(self):
        with patch.object(sys, "argv", ["launcher", "--no-open"]), \
             patch.object(launcher, "healthy", return_value=True), \
             patch.object(launcher.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
            run.assert_not_called()

    def test_daemon_runs_nonroot_outside_home_and_has_restart_policy(self):
        state = install_daemon.STATE
        job = install_daemon.daemon_plist("labstaff", state / "runtime/bin/python", Path("/opt/homebrew/opt/postgresql@18/bin"))
        self.assertEqual(job["UserName"], "labstaff")
        self.assertTrue(job["RunAtLoad"])
        self.assertTrue(job["KeepAlive"])
        self.assertEqual(job["ProgramArguments"][-1], "serve")
        self.assertTrue(str(job["WorkingDirectory"]).startswith("/Library/"))
        encoded = plistlib.dumps(job)
        self.assertNotIn(b"/Users/", encoded)
        self.assertNotIn(b"db_password", encoded)
        self.assertEqual(plistlib.loads(encoded), job)

    def test_prepare_only_never_registers_service(self):
        with tempfile.TemporaryDirectory() as folder:
            args = ["installer", "--user", __import__("pwd").getpwuid(__import__("os").getuid()).pw_name,
                    "--plan-dir", folder]
            with patch.object(sys, "argv", args), patch.object(install_daemon.subprocess, "run") as run, \
                 contextlib.redirect_stdout(io.StringIO()):
                install_daemon.main()
                run.assert_not_called()
            self.assertTrue((Path(folder) / "org.egr.fabric-conditioning.plist").is_file())


if __name__ == "__main__":
    unittest.main()
