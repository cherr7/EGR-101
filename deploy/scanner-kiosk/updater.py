"""Pi self-update, run by station-agent.py.

When the Mac runs a different release, download that release's station package from the Mac, check its
SHA-256, and unpack it next to the running copy. Switch only after the station has been idle for
2 minutes, then restart the kiosk. If the page doesn't come back within 90 s, switch back.

  /opt/fabric-scanner/releases/<version>/{ui,bin}
  /opt/fabric-scanner/releases/current -> <version>    (ui and bin link through it)
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import time
import urllib.request

RELEASES = Path("/opt/fabric-scanner/releases")
IDLE_SECONDS = 120
VERIFY_SECONDS = 90
CHECK_EVERY = 300
RETRY_FAILED_AFTER = 6 * 3600
MAX_BYTES = 64 * 1024 * 1024
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def get_json(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read(65536))


def restart_kiosk():
    subprocess.run(["systemctl", "--user", "--no-block", "restart", "fabric-scanner.target"], check=False, timeout=20)


def schedule_verify(agent):
    """A timer that runs the old release's agent to switch back if the new one never confirms."""
    subprocess.run(["systemd-run", "--user", "--collect", f"--on-active={VERIFY_SECONDS + 60}",
                    f"--unit=fabric-update-verify-{int(time.time())}", "/usr/bin/python3", str(agent), "--verify-update"],
                   check=False, timeout=20, capture_output=True)


class Updater:
    def __init__(self, api_base, releases=RELEASES, watchdog="http://127.0.0.1:8765", restart=restart_kiosk,
                 schedule=schedule_verify, clock=time.time):
        self.api = api_base.rstrip("/")
        self.releases, self.watchdog = Path(releases), watchdog.rstrip("/")
        self.restart, self.schedule, self.clock = restart, schedule, clock
        self.last_check = 0.0

    # ---- state ----
    @property
    def state_file(self):
        return self.releases / "update.json"

    def state(self):
        try:
            value = json.loads(self.state_file.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def record(self, **facts):
        facts["at"] = self.clock()
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(facts))
        os.replace(temporary, self.state_file)

    def report(self):
        """What the Mac shows on System health."""
        value = self.state()
        return {key: value.get(key) for key in ("state", "version", "error")} if value else None

    def managed(self):
        return (self.releases / "current").is_symlink()

    def running(self):
        try:
            return json.loads((self.releases / "current/ui/station-package.json").read_text()).get("release")
        except (OSError, ValueError, AttributeError):
            return None

    def point(self, version):
        temporary = self.releases / ".current.new"
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(version)
        os.replace(temporary, self.releases / "current")

    # ---- loop ----
    def tick(self):
        if not self.managed():
            return
        state = self.state()
        if state.get("state") == "pending":
            return self.verify(state)
        if self.clock() - self.last_check < CHECK_EVERY and not (self.releases / str(state.get("ready", ""))).is_dir():
            return
        self.last_check = self.clock()
        try:
            wanted = get_json(self.api + "/api/version").get("version")
        except (OSError, ValueError, AttributeError):
            return
        if not isinstance(wanted, str) or not VERSION.fullmatch(wanted) or wanted == self.running() or wanted == "unknown":
            return
        if (state.get("version") == wanted and state.get("state") in {"rolled back", "failed"}
                and self.clock() - state.get("at", 0) < RETRY_FAILED_AFTER):
            return
        try:
            if not (self.releases / wanted).is_dir():
                self.fetch(wanted)
        except (OSError, ValueError, tarfile.TarError) as error:
            logging.warning("Update to %s failed: %s", wanted, error)
            self.record(state="failed", version=wanted, error=str(error)[:300])
            return
        if self.idle_seconds() < IDLE_SECONDS:
            if state.get("ready") != wanted:
                self.record(state="waiting", version=wanted, ready=wanted)
            return
        self.switch(wanted)

    def idle_seconds(self):
        try:
            value = get_json(self.watchdog + "/status", timeout=2).get("idle_seconds")
            return value if isinstance(value, int) else 0
        except (OSError, ValueError, AttributeError):
            return 0

    def healthy_age(self):
        try:
            value = get_json(self.watchdog + "/status", timeout=2).get("healthy_age")
            return value if isinstance(value, (int, float)) else None
        except (OSError, ValueError, AttributeError):
            return None

    # ---- download ----
    def fetch(self, version):
        info = get_json(self.api + "/api/station/package/info")
        if info.get("version") != version:
            raise ValueError("The Mac is changing versions; try again later.")
        if not isinstance(info.get("size"), int) or info["size"] > MAX_BYTES:
            raise ValueError("Package is too large.")
        download = self.releases / ".download.tar.gz"
        digest = hashlib.sha256()
        with urllib.request.urlopen(self.api + "/api/station/package", timeout=60) as response, download.open("wb") as stream:
            total = 0
            for chunk in iter(lambda: response.read(1 << 16), b""):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("Package is too large.")
                digest.update(chunk)
                stream.write(chunk)
        try:
            if digest.hexdigest() != info.get("sha256"):
                raise ValueError("Package checksum does not match.")
            partial = self.releases / (".partial-" + version)
            shutil.rmtree(partial, ignore_errors=True)
            unpack(download, partial)
            release = json.loads((partial / "ui/station-package.json").read_text()).get("release")
            if release != version or not (partial / "bin/station-agent.py").is_file():
                shutil.rmtree(partial, ignore_errors=True)
                raise ValueError("Package is for a different version.")
            os.replace(partial, self.releases / version)
        finally:
            download.unlink(missing_ok=True)

    # ---- switch ----
    def switch(self, version):
        previous = os.readlink(self.releases / "current")
        logging.info("Switching from %s to %s", previous, version)
        self.record(state="pending", version=version, previous=previous)
        self.schedule(self.releases / previous / "bin/station-agent.py")
        self.point(version)
        self.restart()

    def verify(self, state, final=False):
        """Confirm the new release once its page reports healthy; otherwise switch back."""
        age = self.healthy_age()
        if age is not None and age < 10 and self.running() == state.get("version"):
            self.record(state="updated", version=state.get("version"))
            self.prune(keep={state.get("version"), state.get("previous")})
            return
        if final or self.clock() - state.get("at", 0) > VERIFY_SECONDS:
            self.rollback(state)

    def rollback(self, state):
        previous = state.get("previous")
        if not previous or not (self.releases / previous).is_dir():
            self.record(state="failed", version=state.get("version"), error="no previous release to return to")
            return
        logging.warning("Update to %s did not come up; switching back to %s", state.get("version"), previous)
        self.point(previous)
        self.record(state="rolled back", version=state.get("version"), error="the page did not come back")
        self.restart()

    def prune(self, keep):
        for folder in self.releases.iterdir():
            if folder.is_dir() and not folder.is_symlink() and not folder.name.startswith(".") and folder.name not in keep:
                shutil.rmtree(folder, ignore_errors=True)


def unpack(archive, destination):
    """Only plain files and folders under ui/ and bin/; nothing absolute, no '..', no links."""
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            if (member.name.startswith("/") or ".." in parts or not parts or parts[0] not in {"ui", "bin"}
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Unexpected file in package: " + member.name)
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(member) as source, target.open("wb") as stream:
                shutil.copyfileobj(source, stream)
            target.chmod(0o755 if member.mode & 0o100 else 0o644)
