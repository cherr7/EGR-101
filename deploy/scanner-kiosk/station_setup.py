#!/usr/bin/env python3
"""What runs on a scan station besides the kiosk, and putting it back when it drifts.

The Pi's own startup list (/etc/xdg/labwc/autostart: desktop, taskbar, on-screen keyboard) always
runs, so Exit kiosk mode lands on a working desktop. The kiosk user's list holds only the screen
rotation and the kiosk. Nothing else should show a full-screen browser or offer remote access.

  station_setup.py autostart ROTATION   print the kiosk user's startup list (install.sh)
"""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

KIOSK_BLOCK = [
    "# fabric-scanner dedicated kiosk",
    "systemctl --user import-environment WAYLAND_DISPLAY DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS",
    "systemctl --user daemon-reload",
    "systemctl --user restart fabric-scanner.target &",
]
ROTATIONS = {"90", "180", "270"}
CONNECT_UNITS = ("rpi-connect.service", "rpi-connect-wayvnc.service", "rpi-connect-signin.path")
CONNECT_PROCESSES = {"rpi-connectd", "wayvnc"}


def autostart_text(rotation):
    """The whole startup list for a rotation of 90/180/270, or None for no rotation."""
    lines = []
    if rotation is not None:
        if rotation not in ROTATIONS:
            raise ValueError("DISPLAY_ROTATION must be 90, 180, 270 or none")
        lines += ["# fabric-scanner landscape",
                  "for output in $(wlr-randr 2>/dev/null | awk '/^DSI-/ {print $1}'); do "
                  f'wlr-randr --output "$output" --transform {rotation}; done',
                  "# end fabric-scanner landscape", ""]
    return "\n".join(lines + KIOSK_BLOCK) + "\n"


def rotation_of(text):
    found = re.search(r"--transform (\d+); done", text or "")
    return found.group(1) if found and found.group(1) in ROTATIONS else None


def autostart_path(home):
    return Path(home) / ".config/labwc/autostart"


def state_dir(environment):
    base = environment.get("XDG_STATE_HOME") or str(Path(environment.get("HOME", "~")) / ".local/state")
    return str(Path(base) / "fabric-scanner")


def processes(proc=Path("/proc")):
    """(pid, name, command line) for every process we can read."""
    found = []
    for entry in proc.iterdir() if proc.is_dir() else []:
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
            name = (entry / "comm").read_text().strip()
        except OSError:
            continue
        found.append((int(entry.name), name, [arg.decode(errors="replace") for arg in args if arg]))
    return found


def stray_browsers(running, own_state_dir):
    """Full-screen Chromium windows that aren't the station's own browser."""
    own = "--user-data-dir=" + own_state_dir
    return [pid for pid, _, args in running
            if args and "chromium" in Path(args[0]).name and "--kiosk" in args
            and not any(arg.startswith("--type=") for arg in args) and own not in args]


def run(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def connect_on(running, systemctl=run):
    if any(name in CONNECT_PROCESSES for _, name, _ in running):
        return True
    states = systemctl(["systemctl", "--user", "is-enabled", *CONNECT_UNITS]).split()
    return any(state in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias", "indirect"} for state in states)


def extras(home, environment, root=Path("/"), running=None, systemctl=run):
    """What this station runs that a station shouldn't. 'restart' fixes itself when the agent starts;
    'reinstall' needs root, so scripts/deploy-pi.sh."""
    running = processes(root / "proc") if running is None else running
    found = []
    path = autostart_path(home)
    try:
        current = path.read_text()
        if current != autostart_text(rotation_of(current)):
            found.append({"item": "extra startup programs", "fix": "restart"})
    except OSError:
        pass
    if stray_browsers(running, state_dir(environment)):
        found.append({"item": "a second kiosk browser", "fix": "restart"})
    if connect_on(running, systemctl):
        found.append({"item": "Raspberry Pi Connect", "fix": "restart"})
    if (root / "etc/nginx/sites-enabled/default").exists():
        found.append({"item": "the nginx welcome page", "fix": "reinstall"})
    if any(name == "rpcbind" for _, name, _ in running):
        found.append({"item": "rpcbind", "fix": "reinstall"})
    return found


def enforce(home, environment, running=None, systemctl=run, kill=os.kill):
    """Put back what the kiosk user may change: the startup list, stray browsers, Pi Connect.
    Leaves the desktop alone, so Exit kiosk mode still works for debugging."""
    running = processes() if running is None else running
    path = autostart_path(home)
    try:
        current = path.read_text()
        wanted = autostart_text(rotation_of(current))
        if current != wanted:
            temporary = path.with_name(path.name + ".new")
            temporary.write_text(wanted)
            temporary.chmod(0o755)
            os.replace(temporary, path)
    except OSError:
        pass
    for pid in stray_browsers(running, state_dir(environment)):
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if connect_on(running, systemctl):
        systemctl(["systemctl", "--user", "mask", "--now", *CONNECT_UNITS])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "autostart":
        try:
            sys.stdout.write(autostart_text(None if sys.argv[2] == "none" else sys.argv[2]))
        except ValueError as error:
            sys.exit(str(error))
    else:
        sys.exit(__doc__)
