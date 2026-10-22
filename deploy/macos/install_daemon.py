#!/usr/bin/env python3
"""Prepare a reviewable system launchd installation; mutate only with --apply."""
import argparse
import os
from pathlib import Path
import plistlib
import pwd
import shutil
import socket
import subprocess
import sys

from build_app import ROOT, copy_code

STATE = Path("/Library/Application Support/Fabric Conditioning")
LABEL = "org.egr.fabric-conditioning"
PLIST = Path("/Library/LaunchDaemons") / (LABEL + ".plist")


def daemon_plist(username, python, postgres_bin):
    return {"Label": LABEL, "UserName": username,
            "ProgramArguments": [str(python), str(STATE / "app/deploy/macos/host.py"), "serve"],
            "WorkingDirectory": str(STATE / "app"),
            "EnvironmentVariables": {"FABRIC_APP_ROOT": str(STATE / "app"),
                "FABRIC_STATE_DIR": str(STATE), "FABRIC_PYTHON": str(python),
                "FABRIC_POSTGRES_BIN": str(postgres_bin), "PYTHONUNBUFFERED": "1",
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15,
            "StandardOutPath": str(STATE / "logs/daemon.log"),
            "StandardErrorPath": str(STATE / "logs/daemon-error.log"),
            "Umask": 0o077, "ExitTimeOut": 30}


def run_as(username, command):
    subprocess.run(["/usr/bin/sudo", "-u", username, "--"] + [str(part) for part in command], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=os.environ.get("SUDO_USER") or os.environ.get("USER"))
    parser.add_argument("--apply", action="store_true", help="Install files and bootstrap the system daemon; requires sudo")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--postgres-bin", default="/opt/homebrew/opt/postgresql@18/bin")
    parser.add_argument("--plan-dir", type=Path, default=ROOT / "output/macOS/daemon-plan")
    args = parser.parse_args()
    if not args.user or args.user == "root":
        parser.error("--user must identify a non-root local account; PostgreSQL cannot run as root")
    account = pwd.getpwnam(args.user)
    pg = Path(args.postgres_bin)
    if not (pg / "pg_ctl").is_file():
        parser.error("Install PostgreSQL 18 or supply --postgres-bin")
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Build the frontend before installing")
    runtime = STATE / "runtime/bin/python"
    plan = daemon_plist(args.user, runtime, pg)
    args.plan_dir.mkdir(parents=True, exist_ok=True)
    with (args.plan_dir / PLIST.name).open("wb") as stream:
        plistlib.dump(plan, stream)
    if not args.apply:
        print("Prepared, without system changes: " + str(args.plan_dir / PLIST.name))
        print("Review it, then run: sudo deploy/macos/install-daemon.sh --apply --user " + args.user)
        return
    if os.geteuid() != 0:
        parser.error("--apply requires sudo")
    if not PLIST.exists():
        for port in [8120, 55432]:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    parser.error("Port " + str(port) + " is active. Stop the development host before switching to the system installation.")
    else:
        subprocess.run(["/bin/launchctl", "bootout", "system/" + LABEL], check=False)
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(STATE, account.pw_uid, account.pw_gid)
    os.chmod(STATE, 0o700)
    logs = STATE / "logs"
    logs.mkdir(exist_ok=True)
    os.chown(logs, account.pw_uid, account.pw_gid)
    copy_code(ROOT, STATE / "app")
    if not runtime.is_file():
        run_as(args.user, [args.python, "-m", "venv", STATE / "runtime"])
    run_as(args.user, [runtime, "-m", "pip", "install", "--disable-pip-version-check", "-r", STATE / "app/backend/requirements.txt"])
    env = ["/usr/bin/env", "FABRIC_APP_ROOT=" + str(STATE / "app"), "FABRIC_STATE_DIR=" + str(STATE),
           "FABRIC_PYTHON=" + str(runtime), "FABRIC_POSTGRES_BIN=" + str(pg)]
    run_as(args.user, env + [runtime, STATE / "app/deploy/macos/host.py", "init"])
    shutil.copy2(args.plan_dir / PLIST.name, PLIST)
    os.chown(PLIST, 0, 0)
    os.chmod(PLIST, 0o644)
    subprocess.run(["/bin/launchctl", "bootstrap", "system", str(PLIST)], check=True)
    print("Installed the startup service. The database and web app run as " + args.user + ".")


if __name__ == "__main__":
    main()
