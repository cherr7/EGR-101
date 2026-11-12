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
        self.idle_seconds = None
        self.idle_seen = None
        self.lock = threading.Lock()

    def observe(self, payload, now):
        fields = ("focused", "visible", "capture_ready")
        if not isinstance(payload, dict) or any(type(payload.get(key)) is not bool for key in fields):
            raise ValueError("Expected boolean focus, visibility, and capture state.")
        bridged = payload.get("input_bridge", False)
        if type(bridged) is not bool:
            raise ValueError("Expected a boolean input bridge state.")
        idle = payload.get("idle_seconds")
        if idle is not None and (type(idle) is not int or idle < 0):
            raise ValueError("Expected whole idle seconds.")
        with self.lock:
            if idle is not None:
                self.idle_seconds, self.idle_seen = idle, now
            # Only the owning, calibrated stream with a usable reader may bypass focus.
            # A connected SSE socket, missing scanner, or another page's stream cannot.
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

    def status(self, now):
        """For the station agent: is the page healthy, and how long since anyone used it."""
        with self.lock:
            idle = None if self.idle_seen is None else self.idle_seconds + int(now - self.idle_seen)
            healthy_age = None if self.last_healthy is None else round(now - self.last_healthy, 1)
            return {"healthy_age": healthy_age, "idle_seconds": idle}

    def restarted(self, now):
        with self.lock:
            self.started = now
            self.last_restart = now
            self.last_healthy = None


EXIT_UNITS = ("fabric-scanner-browser.service", "fabric-scanner-input.service", "fabric-scanner-watchdog.service")


def exit_kiosk():
    """Leave kiosk mode until the next reboot (labwc autostart restarts fabric-scanner.target).
    The watchdog stops too, so it cannot reopen Chromium; the input bridge stops so the scanner
    types on the desktop. The health agent keeps reporting to the Mac."""
    time.sleep(0.5)  # let the 202 reach the page first
    logging.warning("Exit kiosk mode requested from the station page")
    try:
        subprocess.run(["systemctl", "--user", "stop", "--no-block", *EXIT_UNITS], check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        logging.error("Exit kiosk mode failed: %s", error)


def handler_for(health, clock=time.monotonic, on_exit=exit_kiosk):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/status":
                self.send_error(404)
                return
            body = json.dumps(health.status(clock())).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/exit":
                self.send_response(202)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                threading.Thread(target=on_exit, daemon=True).start()
                return
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
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "restart", "fabric-scanner-browser.service"],
                    check=False, timeout=20,
                )
                if result.returncode:
                    logging.error("Browser restart failed with exit %s", result.returncode)
            except (OSError, subprocess.TimeoutExpired) as error:
                # A transient systemd failure must not kill the watchdog itself.
                logging.error("Browser restart failed: %s", error)
        time.sleep(2)


if __name__ == "__main__":
    run()
