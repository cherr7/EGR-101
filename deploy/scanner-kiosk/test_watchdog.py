import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("scanner_watchdog", Path(__file__).with_name("watchdog.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class WatchdogTests(unittest.TestCase):
    def test_startup_then_missing_page(self):
        state = module.Health(100)
        self.assertIsNone(state.restart_reason(144))
        self.assertIn("startup", state.restart_reason(145))

    def test_healthy_page_stays_running(self):
        state = module.Health(0)
        state.observe(dict(focused=True, visible=True, capture_ready=True), 60)
        self.assertIsNone(state.restart_reason(74))

    def test_unfocused_hidden_and_missing_capture_expire(self):
        for field in ("focused", "visible", "capture_ready"):
            with self.subTest(field=field):
                state = module.Health(0)
                state.observe(dict(focused=True, visible=True, capture_ready=True), 60)
                payload = dict(focused=True, visible=True, capture_ready=True)
                payload[field] = False
                state.observe(payload, 70)
                self.assertIsNotNone(state.restart_reason(76))

    def test_restart_gets_new_startup_grace(self):
        state = module.Health(0)
        state.restarted(100)
        self.assertIsNone(state.restart_reason(144))
        self.assertIsNotNone(state.restart_reason(145))

    def test_api_offline_does_not_trigger_restart(self):
        state = module.Health(0)
        state.observe(dict(focused=True, visible=True, capture_ready=True, api_healthy=False), 100)
        self.assertIsNone(state.restart_reason(110))

    def test_malformed_state_rejected(self):
        for payload in ({}, {"focused": "true"}, None):
            with self.assertRaises(ValueError):
                module.Health(0).observe(payload, 50)

    def test_loopback_http_receiver(self):
        health = module.Health(0)
        server = ThreadingHTTPServer(("127.0.0.1", 0), module.handler_for(health, clock=lambda: 60))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = "http://127.0.0.1:" + str(server.server_port) + "/heartbeat"
            request = urllib.request.Request(endpoint, data=json.dumps(dict(focused=True, visible=True, capture_ready=True)).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 204)
            self.assertIsNone(health.restart_reason(70))
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(endpoint, data=b"{}"))
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
