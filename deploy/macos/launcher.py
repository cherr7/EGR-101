#!/usr/bin/env python3
"""Small launcher using only Python's standard library; no browser opens in --check."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

APP_ROOT = Path(os.environ.get("FABRIC_APP_ROOT", Path(__file__).resolve().parents[2]))
SYSTEM_STATE = Path("/Library/Application Support/Fabric Conditioning")


def state_directory():
    return Path(os.environ.get("FABRIC_STATE_DIR",
                SYSTEM_STATE if (SYSTEM_STATE / "config.json").exists()
                else Path.home() / "Library/Application Support/Fabric Conditioning"))


def runtime_path(state):
    supplied = os.environ.get("FABRIC_PYTHON")
    candidates = ([Path(supplied)] if supplied else []) + [state / "runtime/bin/python", APP_ROOT / ".venv/bin/python"]
    return next((path for path in candidates if path.is_file()), None)


def app_url(state):
    try:
        port = json.loads((state / "config.json").read_text())["http_port"]
    except (OSError, ValueError, KeyError):
        port = 8120
    return "http://127.0.0.1:" + str(port)


def healthy(url):
    try:
        with urlopen(url + "/api/health", timeout=2) as response:
            return response.status == 200 and json.load(response).get("database") == "connected"
    except Exception:
        return False


def inspect_installation():
    state = state_directory()
    required = ["backend/app/main.py", "backend/alembic.ini", "frontend/dist/index.html", "deploy/macos/host.py"]
    missing = [name for name in required if not (APP_ROOT / name).is_file()]
    runtime = runtime_path(state)
    return {"app_root": str(APP_ROOT), "state_directory": str(state),
            "url": app_url(state), "runtime": str(runtime) if runtime else None,
            "missing_files": missing, "ready_to_launch": not missing and runtime is not None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Inspect without starting services or opening any UI")
    parser.add_argument("--no-open", action="store_true", help="Start/check the host without opening a browser")
    args = parser.parse_args()
    info = inspect_installation()
    if args.check:
        print(json.dumps(info, indent=2))
        return 0 if info["ready_to_launch"] else 1
    state = state_directory()
    url = app_url(state)
    if not healthy(url):
        if state == SYSTEM_STATE:
            for _ in range(20):
                if healthy(url):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError("The installed background service is unavailable. See the host logs in " + str(state / "logs"))
        else:
            if not info["ready_to_launch"]:
                raise RuntimeError("Run deploy/macos/setup.sh first. Missing runtime or application files.")
            state.mkdir(parents=True, exist_ok=True, mode=0o700)
            (state / "logs").mkdir(exist_ok=True)
            with (state / "launcher.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if not healthy(url):
                    env = os.environ.copy()
                    env.update(FABRIC_APP_ROOT=str(APP_ROOT), FABRIC_STATE_DIR=str(state), FABRIC_PYTHON=info["runtime"])
                    with (state / "logs/launcher.log").open("ab") as log:
                        subprocess.run([info["runtime"], str(APP_ROOT / "deploy/macos/host.py"), "start"],
                                       env=env, stdout=log, stderr=log, check=True)
    if not args.no_open:
        subprocess.run(["/usr/bin/open", url], check=True)
    print(url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.SubprocessError, OSError) as error:
        folder = Path.home() / "Library/Logs/Fabric Conditioning"
        folder.mkdir(parents=True, exist_ok=True)
        message = "Fabric Conditioning could not start: " + str(error) + "\n"
        (folder / "launcher-error.log").write_text(message)
        print(message, file=sys.stderr)
        raise SystemExit(1)
