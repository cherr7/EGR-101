#!/usr/bin/env python3
"""Restart a dedicated kiosk browser when its page stops reporting healthy focus."""
import json
import logging
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Health:
    def __init__(self, now):
        self.started = now
        self.last_healthy = None
        self.last_restart = None
        self.lock = threading.Lock()

    def observe(self, payload, now):
        fields = ("focused", "visible", "capture_ready")
        if not isinstance(payload, dict) or any(type(payload.get(key)) is not bool for key in fields):
            raise ValueError("Expected boolean focus, visibility, and capture state.")
        bridged = payload.get("input_bridge", False)
        if type(bridged) is not bool:
            raise ValueError("Expected a boolean input bridge state.")
        with self.lock:
            # With input-bridge.py connected, scans reach the page without browser focus,
            # so only a hidden page or missing capture calls for a restart.
            if payload["visible"] and payload["capture_ready"] and (payload["focused"] or bridged):
                self.last_healthy = now

    def restart_reason(self, now):
        with self.lock:
            if now - self.started < 45:
                return None
            if self.last_restart is not None and now - self.last_restart < 30:
                return None
            if self.last_healthy is None:
                return "no healthy page after startup"
            if now - self.last_healthy > 15:
                return "page unresponsive, unfocused, hidden, or capture missing"
            return None

    def restarted(self, now):
        with self.lock:
            self.started = now
            self.last_restart = now
            self.last_healthy = None


def handler_for(health, clock=time.monotonic):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/heartbeat":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024:
                    raise ValueError("Invalid heartbeat length.")
                health.observe(json.loads(self.rfile.read(length)), clock())
            except (ValueError, json.JSONDecodeError):
                self.send_error(400)
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def log_message(self, *_args):
            pass

    return Handler


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    health = Health(time.monotonic())
    server = ThreadingHTTPServer(("127.0.0.1", 8765), handler_for(health))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        now = time.monotonic()
        reason = health.restart_reason(now)
        if reason:
            logging.warning("Restarting kiosk browser: %s", reason)
            health.restarted(now)
            result = subprocess.run(
                ["systemctl", "--user", "restart", "fabric-scanner-browser.service"],
                check=False, timeout=20,
            )
            if result.returncode:
                logging.error("Browser restart failed with exit %s", result.returncode)
        time.sleep(2)


if __name__ == "__main__":
    run()
