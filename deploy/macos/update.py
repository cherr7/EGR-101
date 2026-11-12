#!/usr/bin/env python3
"""Update the Mac host to a release, with a backup first and automatic rollback.

  python3 deploy/macos/update.py              newest release tag (v*) on origin/main
  python3 deploy/macos/update.py --ref v2026.11.12-1
  python3 deploy/macos/update.py --ref origin/main    the tip of main, before a tag exists
  python3 deploy/macos/update.py --check      say what would happen; change nothing
  python3 deploy/macos/update.py --status     the running release and the last update

Run it from the git checkout on the Mac (~/scanner_host). Each release is built into its own
folder, <state>/releases/<version>, from a clean copy of that commit; <state>/releases/current
points at the one that runs. The server always starts from releases/current/deploy/macos/host.py.

Steps: fetch; refuse anything not on origin/main; build the release while the old one keeps
running; stop if the database's migration is unknown to the new code (nothing touched yet);
back up to ~/fabric-backups; stop, switch, start; if the new release is not healthy, switch back
and start the old one. The result is kept in <state>/update-status.json for System health.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.version import stamp, write  # noqa: E402
from build_app import copy_code  # noqa: E402


def load_host(root):
    spec = importlib.util.spec_from_file_location("fabric_host_" + hashlib.sha1(str(root).encode()).hexdigest()[:8],
                                                  Path(root) / "deploy/macos/host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args, cwd=REPO):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def say(message):
    print("==> " + message, flush=True)


def releases_dir(host):
    return host.state_dir() / "releases"


def running_release(host):
    current = releases_dir(host) / "current"
    if current.is_symlink() or current.exists():
        return current.resolve()
    return None


def release_version(folder):
    try:
        return json.loads((Path(folder) / "VERSION.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def pick_target(ref):
    git("fetch", "--quiet", "--tags", "--force", "origin")
    if not ref:
        tags = [tag for tag in git("tag", "--merged", "origin/main", "--sort=-creatordate", "--list", "v[0-9]*").splitlines() if tag]
        if not tags:
            raise RuntimeError("No release tag on main yet. Tag one (git tag v2026.11.12-1 && git push --tags), "
                               "or pass --ref origin/main.")
        ref = tags[0]
    commit = git("rev-parse", "--verify", ref + "^{commit}")
    if subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", commit, "origin/main"]).returncode != 0:
        raise RuntimeError(f"{ref} is not on main. Only merged code is installed on the Mac.")
    return ref, commit


def build_release(commit, destination):
    """Check the commit out on its own, build its web app, and copy the runnable code into destination."""
    with tempfile.TemporaryDirectory(prefix="fabric-release-") as folder:
        tree = Path(folder) / "tree"
        git("worktree", "add", "--detach", "--quiet", str(tree), commit)
        try:
            for command in (["npm", "ci", "--no-audit", "--no-fund"], ["npm", "run", "build"]):
                subprocess.run(command, cwd=tree / "frontend", check=True, stdout=subprocess.DEVNULL)
            partial = destination.with_name(destination.name + ".partial")
            shutil.rmtree(partial, ignore_errors=True)
            copy_code(tree, partial)
            facts = stamp(tree)
            write(partial, facts)
            shutil.rmtree(destination, ignore_errors=True)
            partial.rename(destination)
            return facts
        finally:
            git("worktree", "remove", "--force", str(tree))


def database_revision(host):
    """The migration the live database is at, read with the host's own database settings."""
    snippet = ("import os, sqlalchemy as s; e = s.create_engine(os.environ['DATABASE_URL']);\n"
               "with e.connect() as c: print(c.execute(s.text('SELECT version_num FROM alembic_version')).scalar() or '')")
    result = subprocess.run([host.python_bin(), "-c", snippet], env=host.environment(), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Cannot read the database's migration. Is the app running? " + result.stderr.strip()[-300:])
    return result.stdout.strip() or None


def known_revisions(release, host):
    snippet = ("import sys; from alembic.config import Config; from alembic.script import ScriptDirectory;\n"
               "print(' '.join(r.revision for r in ScriptDirectory.from_config(Config('alembic.ini')).walk_revisions()))")
    result = subprocess.run([host.python_bin(), "-c", snippet], cwd=release / "backend", capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("Cannot read the new release's migrations: " + result.stderr.strip()[-300:])
    return set(result.stdout.split())


def ensure_requirements(release, previous, host):
    new = (release / "backend/requirements.txt").read_bytes()
    if previous and (previous / "backend/requirements.txt").is_file() and (previous / "backend/requirements.txt").read_bytes() == new:
        return
    say("Installing the new release's Python packages")
    subprocess.run([host.python_bin(), "-m", "pip", "install", "--quiet", "-r", str(release / "backend/requirements.txt")], check=True)


def point_current(target, releases):
    link = releases / "current"
    temporary = releases / ".current.new"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target.name)
    os.replace(temporary, link)   # atomic: current always names a whole release


def record(host, **facts):
    path = host.state_dir() / "update-status.json"
    facts["at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    path.write_text(json.dumps(facts, indent=2) + "\n")


def start_release(release):
    """Start the server from a release with its own host.py; True if it came up healthy."""
    env = {**os.environ, "FABRIC_APP_ROOT": str(release)}
    env.pop("FABRIC_PYTHON", None)
    return subprocess.run([sys.executable, str(release / "deploy/macos/host.py"), "start"], env=env).returncode == 0


def stop_running():
    host = load_host(REPO)
    host.stop()


def update(ref, check_only):
    host = load_host(REPO)
    releases = releases_dir(host)
    previous = running_release(host)
    previous_version = release_version(previous) if previous else (stamp(REPO)["version"] + " (git checkout)")
    ref, commit = pick_target(ref)
    target_version = git("describe", "--tags", "--always", "--match", "v[0-9]*", commit)
    say(f"Running: {previous_version}. Target: {target_version} ({ref})")
    if previous and release_version(previous) == target_version:
        say("Already on this release. Nothing to do.")
        return 0
    if not host.healthy():
        raise RuntimeError("The app is not running or not healthy now. Start it first, so a backup can be taken.")
    current_revision = database_revision(host)
    if check_only:
        say(f"Database migration: {current_revision}. Run without --check to build, back up and switch.")
        return 0

    releases.mkdir(parents=True, exist_ok=True)
    release = releases / target_version
    say(f"Building {target_version} in {release} (the app keeps running)")
    build_release(commit, release)

    say("Checking the database can be upgraded by the new release")
    if current_revision and current_revision not in known_revisions(release, host):
        record(host, result="refused", version=target_version, previous=previous_version,
               reason=f"database migration {current_revision} is unknown to {target_version}")
        raise RuntimeError(f"Stopped before changing anything: the database is at migration {current_revision}, which "
                           f"{target_version} does not know. Updating would fail to start. Nothing was changed.")
    ensure_requirements(release, previous, host)

    stamp_now = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = Path(os.environ.get("FABRIC_BACKUP_DIR", Path.home() / "fabric-backups")) / f"before-{target_version}-{stamp_now}.zip"
    say(f"Backing up to {backup}")
    host.export_checkpoint(backup)

    say("Switching: stop, point to the new release, start")
    stop_running()
    point_current(release, releases)
    if start_release(release):
        record(host, result="updated", version=target_version, previous=previous_version, backup=str(backup))
        say(f"Updated to {target_version}. Backup: {backup}")
        prune(releases, keep={release.name, previous.name if previous else ""})
        return 0

    say("The new release did not come up healthy. Rolling back.")
    load_host(release).stop()
    if previous:
        point_current(previous, releases)
        ok = start_release(previous)
    else:
        (releases / "current").unlink(missing_ok=True)
        ok = subprocess.run([sys.executable, str(REPO / "deploy/macos/host.py"), "start"]).returncode == 0
    record(host, result="rolled back", version=target_version, previous=previous_version, backup=str(backup),
           reason="the new release did not report healthy", restored=ok)
    raise RuntimeError(f"Update to {target_version} failed and was rolled back to {previous_version}"
                       + ("" if ok else " (but the old release did not start either: see the app log)")
                       + f". The backup taken first is {backup}.")


def prune(releases, keep, count=3):
    """Keep the newest few releases (plus the running and previous ones) so rollback stays possible."""
    folders = sorted((p for p in releases.iterdir() if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders[count:]:
        if folder.name not in keep:
            shutil.rmtree(folder, ignore_errors=True)


def status():
    host = load_host(REPO)
    running = running_release(host)
    try:
        last = json.loads((host.state_dir() / "update-status.json").read_text())
    except (OSError, ValueError):
        last = None
    print(json.dumps({"running": release_version(running) if running else "git checkout (no release installed yet)",
                      "release_folder": str(running) if running else None, "last_update": last}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", help="a release tag, or origin/main")
    parser.add_argument("--check", action="store_true", help="report what would happen; change nothing")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        return status()
    return update(args.ref, args.check)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(1)
