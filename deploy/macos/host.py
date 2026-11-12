#!/usr/bin/env python3
"""Native Mac host: one private PostgreSQL cluster and the shared FastAPI/web process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen

# PostgreSQL refuses to start without a valid locale ("postmaster became multithreaded during
# startup"), and Finder, launchd and some shells start processes with none set.
if not (os.environ.get("LC_ALL") or os.environ.get("LANG")):
    os.environ["LC_ALL"] = "en_US.UTF-8"
ROOT = Path(os.environ.get("FABRIC_APP_ROOT", Path(__file__).resolve().parents[2]))
DEFAULT_STATE = Path.home() / "Library/Application Support/Fabric Conditioning"
SYSTEM_STATE = Path("/Library/Application Support/Fabric Conditioning")


def state_dir():
    return Path(os.environ.get("FABRIC_STATE_DIR", SYSTEM_STATE if (SYSTEM_STATE / "config.json").exists() else DEFAULT_STATE))


def pg_bin():
    configured = os.environ.get("FABRIC_POSTGRES_BIN")
    for folder in [configured, "/opt/homebrew/opt/postgresql@18/bin", "/usr/local/opt/postgresql@18/bin"]:
        if folder and (Path(folder) / "pg_ctl").is_file():
            return Path(folder)
    found = shutil.which("pg_ctl")
    if found:
        return Path(found).parent
    raise RuntimeError("Install PostgreSQL 18 first: brew install postgresql@18")


def python_bin():
    configured = os.environ.get("FABRIC_PYTHON")
    candidates = [Path(configured)] if configured else []
    candidates += [ROOT / ".venv/bin/python", state_dir() / "runtime/bin/python"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("Run deploy/macos/setup.sh to install the Python runtime.")


def config():
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / "logs").mkdir(exist_ok=True)
    path = folder / "config.json"
    if not path.exists():
        value = {"db_user": "fabric", "db_name": "fabric_conditioning",
                 "db_port": 55432, "http_port": 8120, "db_password": secrets.token_urlsafe(32)}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
    return json.loads(path.read_text())


def environment(database=None):
    cfg = config()
    env = os.environ.copy()
    name = database or cfg["db_name"]
    env.update(PGHOST="127.0.0.1", PGPORT=str(cfg["db_port"]), PGUSER=cfg["db_user"],
               PGPASSWORD=cfg["db_password"], PGDATABASE=name,
               DATABASE_URL="postgresql+psycopg://" + cfg["db_user"] + ":" + quote(cfg["db_password"], safe="") +
               "@127.0.0.1:" + str(cfg["db_port"]) + "/" + name,
               FRONTEND_DIST=str(ROOT / "frontend/dist"),
               FABRIC_STATE_DIR=str(state_dir()), COOKIE_SECURE="false")
    return env


def run(command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, **kwargs)


def init_database():
    cfg, folder, binaries = config(), state_dir(), pg_bin()
    data = folder / "postgres"
    if not (data / "PG_VERSION").exists():
        password = folder / "init-password"
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(cfg["db_password"] + "\n")
            run([binaries / "initdb", "-D", data, "--username", cfg["db_user"],
                 "--encoding=UTF8", "--locale=C", "--auth-local=scram-sha-256",
                 "--auth-host=scram-sha-256", "--pwfile", password], stdout=subprocess.DEVNULL)
        finally:
            password.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a") as stream:
            stream.write("\nlisten_addresses = '127.0.0.1'\nport = " + str(cfg["db_port"]) +
                         "\nunix_socket_directories = ''\n")
    if subprocess.run([str(binaries / "pg_ctl"), "-D", str(data), "status"],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        run([binaries / "pg_ctl", "-D", data, "-l", folder / "logs/postgres.log", "-w", "start"],
            stdout=subprocess.DEVNULL)
    env = environment("postgres")
    existing = subprocess.check_output([str(binaries / "psql"), "-At", "-c",
                                       "SELECT datname FROM pg_database"], env=env, text=True).splitlines()
    for name in [cfg["db_name"], cfg["db_name"] + "_test"]:
        if name not in existing:
            run([binaries / "createdb", name], env=env, stdout=subprocess.DEVNULL)


def migrate():
    run([python_bin(), "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend", env=environment())


def healthy():
    try:
        with urlopen("http://127.0.0.1:" + str(config()["http_port"]) + "/api/health", timeout=2) as response:
            return response.status == 200 and json.load(response).get("database") == "connected"
    except Exception:
        return False


def export_checkpoint(destination):
    """Export the same verified portable archive as the staff interface."""
    destination = Path(destination).expanduser().resolve()
    if destination.suffix.lower() != ".zip":
        raise RuntimeError("Use a .zip destination for a portable checkpoint.")
    if destination.exists():
        raise RuntimeError("Refusing to overwrite an existing checkpoint: " + str(destination))
    base = "http://127.0.0.1:" + str(config()["http_port"]) + "/api/backups"
    try:
        with urlopen(Request(base, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"), timeout=120) as response:
            record = json.load(response)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".fabric-backup-", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                checksum = hashlib.sha256()
                with urlopen(base + "/" + record["id"] + "/download", timeout=120) as response:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        stream.write(chunk)
                        checksum.update(chunk)
                if checksum.hexdigest() != record["sha256"]:
                    raise RuntimeError("Checkpoint checksum failed; no export was installed.")
                stream.flush()
                os.fsync(stream.fileno())
                # A hard link installs atomically and refuses a destination created concurrently.
                os.link(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError("Checkpoint export failed. Start the host and check the destination: " + str(error)) from error
    return destination


def api_pid():
    try:
        pid = int((state_dir() / "api.pid").read_text())
        command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="],
                                          stderr=subprocess.DEVNULL, text=True)
        if "uvicorn" in command and "app.main:app" in command and str(config()["http_port"]) in command:
            return pid
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    return None


def start_api():
    if api_pid():
        return
    cfg = config()
    with (state_dir() / "logs/app.log").open("ab") as log:
        process = subprocess.Popen([python_bin(), "-m", "uvicorn", "app.main:app",
                                    "--host", "0.0.0.0", "--port", str(cfg["http_port"]),
                                    "--no-server-header"], cwd=ROOT / "backend", env=environment(),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    (state_dir() / "api.pid").write_text(str(process.pid))


def wait_ready():
    for _ in range(60):
        if healthy():
            return
        if not api_pid():
            break
        time.sleep(0.5)
    raise RuntimeError("The app did not become ready. See " + str(state_dir() / "logs/app.log"))


def stop():
    pid = api_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not api_pid():
                break
            time.sleep(0.1)
    (state_dir() / "api.pid").unlink(missing_ok=True)
    data = state_dir() / "postgres"
    if (data / "PG_VERSION").exists():
        subprocess.run([str(pg_bin() / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serve():
    stopping = False

    def on_signal(_number, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    init_database()
    migrate()
    while not stopping:
        init_database()
        start_api()
        for _ in range(5):
            if stopping:
                break
            time.sleep(1)
    stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "start", "stop", "status", "serve", "backup", "test"])
    parser.add_argument("destination", nargs="?")
    args = parser.parse_args()
    if args.command == "init":
        init_database()
        print("Private PostgreSQL cluster ready; application and isolated test databases exist.")
    elif args.command == "start":
        init_database()
        migrate()
        start_api()
        wait_ready()
        print("Staff app: http://127.0.0.1:" + str(config()["http_port"]))
        print("LAN URL: http://" + socket.gethostname().removesuffix(".local") + ".local:" + str(config()["http_port"]))
    elif args.command == "stop":
        stop()
        print("Fabric API and its private PostgreSQL cluster stopped.")
    elif args.command == "status":
        print(json.dumps({"ready": healthy(), "api_pid": api_pid(),
                          "url": "http://127.0.0.1:" + str(config()["http_port"]),
                          "state_directory": str(state_dir())}, indent=2))
    elif args.command == "serve":
        serve()
    elif args.command == "test":
        init_database()
        env = environment(config()["db_name"] + "_test")
        env["TEST_DATABASE_URL"] = env["DATABASE_URL"]
        run([python_bin(), "-m", "pytest", "-q"], cwd=ROOT / "backend", env=env)
    elif args.command == "backup":
        if not args.destination:
            parser.error("backup requires a new destination .zip file")
        print("Checkpoint written: " + str(export_checkpoint(args.destination)))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
