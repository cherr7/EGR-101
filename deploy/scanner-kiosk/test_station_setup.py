import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("station_setup", Path(__file__).with_name("station_setup.py"))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

# The Shared Pi's list on 2026-11-19: our installer had copied the Pi's own list (each desktop program
# started twice), and the hand-made setup had added a second kiosk browser.
SHARED = """# fabric-scanner landscape
for output in $(wlr-randr 2>/dev/null | awk '/^DSI-/ {print $1}'); do wlr-randr --output "$output" --transform 270; done
# end fabric-scanner landscape
/usr/bin/lwrespawn /usr/bin/pcmanfm-pi &
/usr/bin/lwrespawn /usr/bin/wf-panel-pi &
/usr/bin/kanshi &
/usr/bin/lxsession-xdg-autostart
"/home/admin/kiosk/kiosk.sh" &

# fabric-scanner dedicated kiosk
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
systemctl --user daemon-reload
systemctl --user restart fabric-scanner.target &
"""
CLEAN = """# fabric-scanner landscape
for output in $(wlr-randr 2>/dev/null | awk '/^DSI-/ {print $1}'); do wlr-randr --output "$output" --transform 270; done
# end fabric-scanner landscape

# fabric-scanner dedicated kiosk
systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
systemctl --user daemon-reload
systemctl --user restart fabric-scanner.target &
"""
OWN = "/home/admin/.local/state/fabric-scanner"
RUNNING = [
    (10, "chromium", ["/usr/lib/chromium/chromium", "--kiosk", "--user-data-dir=" + OWN, "file:///start"]),
    (11, "chromium", ["/usr/lib/chromium/chromium", "--type=renderer", "--kiosk"]),
    (12, "chromium", ["/usr/lib/chromium/chromium", "--kiosk", "http://10.194.27.78:8120/"]),
    (13, "chromium", ["/usr/lib/chromium/chromium", "https://example.org"]),   # opened from the desktop: left alone
    (14, "wayvnc", ["/usr/bin/wayvnc"]),
    (15, "pcmanfm", ["pcmanfm", "--desktop"]),
]
ENV = {"HOME": "/home/admin"}


class StationSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.list = self.home / ".config/labwc/autostart"
        self.list.parent.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_startup_list_is_rotation_and_kiosk_only(self):
        self.assertEqual(setup.autostart_text("270"), CLEAN)
        self.assertNotIn("landscape", setup.autostart_text(None))
        with self.assertRaises(ValueError):
            setup.autostart_text("45")

    def test_installer_and_agent_write_the_same_list(self):
        printed = subprocess.run([sys.executable, str(Path(__file__).with_name("station_setup.py")), "autostart", "270"],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(printed, CLEAN)
        installer = Path(__file__).with_name("install.sh").read_text()
        self.assertIn('station_setup.py" autostart "$rotation" > "$autostart.new"', installer)
        self.assertNotIn("cp /etc/xdg/labwc/autostart", installer)

    def test_only_a_stray_full_screen_browser_is_stopped(self):
        self.assertEqual(setup.stray_browsers(RUNNING, OWN), [12])

    def test_enforce_rewrites_list_stops_stray_browser_and_pi_connect(self):
        self.list.write_text(SHARED)
        killed, commands = [], []
        systemctl = lambda args: commands.append(args) or "enabled\n"
        setup.enforce(self.home, {"HOME": str(self.home), "XDG_STATE_HOME": "/home/admin/.local/state"},
                      running=RUNNING, systemctl=systemctl, kill=lambda pid, sig: killed.append(pid))
        self.assertEqual(self.list.read_text(), CLEAN)
        self.assertEqual(self.list.stat().st_mode & 0o777, 0o755)
        self.assertEqual(killed, [12])
        self.assertIn(["systemctl", "--user", "mask", "--now", *setup.CONNECT_UNITS], commands)

    def test_clean_station_is_left_untouched_and_reports_nothing(self):
        self.list.write_text(CLEAN)
        before = self.list.stat().st_mtime_ns
        running = [row for row in RUNNING if row[0] in {10, 11, 13, 15}]
        commands = []
        systemctl = lambda args: commands.append(args) or "masked\nmasked\nmasked\n"
        env = {"HOME": str(self.home), "XDG_STATE_HOME": "/home/admin/.local/state"}
        setup.enforce(self.home, env, running=running, systemctl=systemctl, kill=lambda *a: self.fail("killed"))
        self.assertEqual(self.list.stat().st_mtime_ns, before)
        self.assertFalse(any("mask" in args for args in commands))
        self.assertEqual(setup.extras(self.home, env, root=self.home, running=running, systemctl=systemctl), [])

    def test_extras_names_each_problem_and_its_fix(self):
        self.list.write_text(SHARED)
        (self.home / "etc/nginx/sites-enabled").mkdir(parents=True)
        (self.home / "etc/nginx/sites-enabled/default").write_text("")
        running = RUNNING + [(20, "rpcbind", ["/sbin/rpcbind"])]
        found = setup.extras(self.home, {"HOME": str(self.home), "XDG_STATE_HOME": "/home/admin/.local/state"},
                             root=self.home, running=running, systemctl=lambda args: "masked\n")
        self.assertEqual([(row["item"], row["fix"]) for row in found], [
            ("extra startup programs", "restart"), ("a second kiosk browser", "restart"),
            ("Raspberry Pi Connect", "restart"), ("the nginx welcome page", "reinstall"), ("rpcbind", "reinstall")])

    def test_installer_turns_off_what_a_station_does_not_need(self):
        installer = Path(__file__).with_name("install.sh").read_text()
        self.assertIn("rm -f /etc/nginx/sites-enabled/default", installer)
        self.assertIn("systemctl --global mask rpi-connect.service", installer)
        self.assertIn("systemctl disable --now rpcbind.service", installer)
        browser = Path(__file__).with_name("browser.sh").read_text()
        self.assertNotIn("pkill", browser)   # cleanup belongs to the agent, not every browser start


if __name__ == "__main__":
    unittest.main()
