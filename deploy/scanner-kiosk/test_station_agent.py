import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

spec = importlib.util.spec_from_file_location("station_agent", Path(__file__).with_name("station-agent.py"))
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

FILES = {
    "proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n",
    "proc/meminfo": "MemTotal:        4000000 kB\nMemFree:          500000 kB\nMemAvailable:    3000000 kB\nSwapTotal:        102400 kB\nSwapFree:          51200 kB\n",
    "proc/uptime": "1234.56 4000.00\n",
    "proc/loadavg": "0.52 0.40 0.31 1/250 1000\n",
    "proc/mounts": "rootfs / rootfs rw 0 0\n/dev/mmcblk0p2 / ext4 ro,noatime 0 0\n",
    "proc/net/route": "Iface\tDestination\tGateway\nwlan0\t00000000\t0100A8C0\nwlan0\t0000A8C0\t00000000\n",
    "proc/net/wireless": "Inter-| sta-|   Quality        |\n face | tus | link level noise |\n wlan0: 0000   48.  -62.  -256        0      0      0      0     12        0\n",
    "proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
    "sys/class/thermal/thermal_zone0/temp": "71250\n",
    "sys/devices/platform/soc/soc:firmware/get_throttled": "50005\n",
    "etc/os-release": 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\n',
    "opt/fabric-scanner/ui/station-package.json": json.dumps({"built_at": "2026-11-10T20:00:00+00:00", "release": "v2026.11.12-1"}),
}


class StationAgentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name, content in FILES.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(content)

    def tearDown(self):
        self.temporary.cleanup()

    def test_report_reads_pi_health_from_proc_and_sys(self):
        before = agent.cpu_ticks(self.root)
        (self.root / "proc/stat").write_text("cpu  150 0 150 900 0 0 0 0 0 0\n")
        _, report = agent.report(self.root, before)
        self.assertEqual(report["cpu_percent"], 50.0)  # +100 busy of +200 total
        self.assertEqual(report["memory"]["total_bytes"], 4000000 * 1024)
        self.assertEqual(report["memory"]["percent"], 25.0)
        self.assertEqual(report["memory"]["swap_used_bytes"], 51200 * 1024)
        self.assertEqual(report["temperature_c"], 71.2)
        self.assertEqual(report["throttled"]["flags"], ["under_voltage", "throttled", "under_voltage_occurred", "throttled_occurred"])
        self.assertTrue(report["disk"]["read_only"])  # the active (last) "/" mount is ro
        self.assertEqual(report["network"], {"interface": "wlan0", "wifi_signal_dbm": -62, "wired": False})
        self.assertEqual(report["model"], "Raspberry Pi 5 Model B Rev 1.0")
        self.assertEqual(report["os"], "Debian GNU/Linux 13 (trixie)")
        self.assertEqual(report["uptime_seconds"], 1234)
        self.assertEqual(report["load_average"], [0.52, 0.40, 0.31])
        self.assertEqual(report["package_built_at"], "2026-11-10T20:00:00+00:00")
        self.assertEqual(report["package_version"], "v2026.11.12-1")
        self.assertIsNone(report["services"])  # only queried on a real Pi root

    def test_missing_files_give_nulls_not_errors(self):
        empty = self.root / "empty"
        empty.mkdir()
        _, report = agent.report(empty, None)
        for key in ("cpu_percent", "memory", "temperature_c", "throttled", "model", "uptime_seconds", "load_average"):
            self.assertIsNone(report[key], key)
        self.assertEqual(report["network"]["interface"], None)

    def test_station_id_from_env_or_legacy_kiosk_url(self):
        self.assertEqual(agent.station_id({"STATION_ID": "ppe-door"}), "ppe-door")
        self.assertEqual(agent.station_id({"KIOSK_URL": "http://127.0.0.1:8080/station.html?station=footwear-door#kiosk"}), "footwear-door")
        for bad in ({}, {"STATION_ID": "../x"}):
            with self.assertRaises(SystemExit):
                agent.station_id(bad)

    def test_once_posts_report_to_station_device_endpoint(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            import os
            os.environ["STATION_ID"] = "shared-door"
            code = agent.main(["--once", "--root", str(self.root), "--url", f"http://127.0.0.1:{server.server_port}"])
        finally:
            server.shutdown()
            os.environ.pop("STATION_ID", None)
        self.assertEqual(code, 0)
        self.assertEqual(received[0][0], "/api/stations/shared-door/device")
        self.assertEqual(received[0][1]["agent_version"], 1)

    def test_unreachable_host_is_reported_not_raised(self):
        import os
        os.environ["STATION_ID"] = "shared-door"
        try:
            self.assertEqual(agent.main(["--once", "--root", str(self.root), "--url", "http://127.0.0.1:9"]), 1)
        finally:
            os.environ.pop("STATION_ID", None)

    def test_installer_and_target_include_the_agent(self):
        folder = Path(__file__).parent
        script = (folder / "install.sh").read_text()
        self.assertIn('"$source_dir/station-agent.py" "$source_dir/updater.py" "$releases/$release/bin/"', script)
        self.assertIn("fabric-scanner-agent.service; do", script)
        self.assertIn('"STATION_ID=" + station', script)
        self.assertIn('"apiOrigin": api_origin', script)
        self.assertIn("fabric-scanner-agent.service", (folder / "fabric-scanner.target").read_text())
        unit = (folder / "fabric-scanner-agent.service").read_text()
        self.assertIn("EnvironmentFile=-/opt/fabric-scanner/station.env", unit)
        self.assertIn("PartOf=fabric-scanner.target", unit)


if __name__ == "__main__":
    unittest.main()
