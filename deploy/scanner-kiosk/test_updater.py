"""Pi self-update against a fake Mac and watchdog on localhost; nothing outside a temp folder changes."""
import hashlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tarfile
import tempfile
import threading
import unittest

import updater


def make_package(version, extra=None):
    buffer = io.BytesIO()
    files = {"ui/station-package.json": json.dumps({"release": version}).encode(), "ui/index.html": b"<html>",
             "bin/station-agent.py": b"print('agent')\n", **(extra or {})}
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755 if name.startswith("bin/") else 0o644
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Fake:
    def __init__(self):
        self.version, self.package, self.sha = "v2", make_package("v2"), None
        self.idle, self.healthy_age = 0, None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/api/version":
                    body = json.dumps({"version": fake.version}).encode()
                elif self.path == "/api/station/package/info":
                    body = json.dumps({"version": fake.version, "size": len(fake.package),
                                       "sha256": fake.sha or hashlib.sha256(fake.package).hexdigest()}).encode()
                elif self.path == "/api/station/package":
                    body = fake.package
                elif self.path == "/status":
                    body = json.dumps({"idle_seconds": fake.idle, "healthy_age": fake.healthy_age}).encode()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        self.addCleanup(self.fake.server.shutdown)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.releases = Path(folder.name)
        (self.releases / "v1/ui").mkdir(parents=True)
        (self.releases / "v1/ui/station-package.json").write_text(json.dumps({"release": "v1"}))
        (self.releases / "current").symlink_to("v1")
        self.now, self.restarts, self.scheduled = 1000.0, 0, []
        self.updater = updater.Updater(self.fake.url, releases=self.releases, watchdog=self.fake.url,
                                       restart=self.restart, schedule=self.scheduled.append, clock=lambda: self.now)

    def restart(self):
        self.restarts += 1

    def test_waits_for_idle_then_switches_and_confirms(self):
        self.updater.tick()
        self.assertTrue((self.releases / "v2/bin/station-agent.py").is_file())
        self.assertEqual(self.updater.running(), "v1")
        self.assertEqual(self.updater.state()["state"], "waiting")
        self.fake.idle = 300
        self.updater.tick()
        self.assertEqual(self.updater.running(), "v2")
        self.assertEqual(self.restarts, 1)
        self.assertEqual(self.scheduled, [self.releases / "v1/bin/station-agent.py"])
        self.fake.healthy_age = 2
        self.updater.tick()
        self.assertEqual(self.updater.state()["state"], "updated")
        self.assertTrue((self.releases / "v1").is_dir())          # kept for a manual rollback

    def test_switches_back_when_the_page_does_not_come_up(self):
        self.fake.idle = 300
        self.updater.tick()
        self.assertEqual(self.updater.running(), "v2")
        self.now += updater.VERIFY_SECONDS + 1
        self.updater.tick()
        self.assertEqual(self.updater.running(), "v1")
        self.assertEqual(self.updater.state()["state"], "rolled back")
        self.assertEqual(self.restarts, 2)
        self.now += 60
        self.updater.last_check = 0
        self.updater.tick()                                        # does not retry the same version right away
        self.assertEqual(self.updater.running(), "v1")

    def test_bad_checksum_is_refused(self):
        self.fake.sha = "0" * 64
        self.fake.idle = 300
        self.updater.tick()
        self.assertFalse((self.releases / "v2").exists())
        self.assertEqual(self.updater.state()["state"], "failed")
        self.assertEqual(self.updater.running(), "v1")

    def test_package_cannot_write_outside_its_folder(self):
        self.fake.package = make_package("v2", {"../escape.txt": b"x"})
        self.updater.tick()
        self.assertFalse((self.releases.parent / "escape.txt").exists())
        self.assertEqual(self.updater.state()["state"], "failed")

    def test_old_layout_is_left_alone(self):
        (self.releases / "current").unlink()
        self.updater.tick()
        self.assertFalse((self.releases / "v2").exists())


if __name__ == "__main__":
    unittest.main()
