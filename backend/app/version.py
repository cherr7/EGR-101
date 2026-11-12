"""Which release this server is running.

The build writes VERSION.json at the top of the code it packages (the Mac app's Resources/app,
and the Pi station package carries the same string). Running from a git checkout instead, the
version is read from git. Release tags look like v2026.11.12-1; between releases the version is
the last tag plus the commit (v2026.11.12-1-3-gabc1234), or just the commit with no tag yet.
"""
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
FILE = "VERSION.json"


def from_git(root=ROOT):
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip()
    try:
        commit = git("rev-parse", "--short=7", "HEAD")
        version = git("describe", "--tags", "--always", "--dirty", "--match", "v[0-9]*")
    except (OSError, subprocess.SubprocessError):
        return None
    return {"version": version, "commit": commit}


def stamp(root=ROOT):
    """The version facts to write into a build: from git, or 'unknown' outside a checkout."""
    facts = from_git(root) or {"version": "unknown", "commit": None}
    return {**facts, "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}


def write(destination, facts):
    Path(destination, FILE).write_text(json.dumps(facts, indent=2) + "\n")


@lru_cache(maxsize=1)
def current():
    try:
        facts = json.loads((ROOT / FILE).read_text())
        if isinstance(facts, dict) and isinstance(facts.get("version"), str):
            return {"version": facts["version"], "commit": facts.get("commit"), "built_at": facts.get("built_at")}
    except (OSError, ValueError):
        pass
    return {**(from_git() or {"version": "unknown", "commit": None}), "built_at": None}


if __name__ == "__main__":
    print(current()["version"])
