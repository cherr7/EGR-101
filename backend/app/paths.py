"""Where the Mac host keeps its settings and state (set by deploy/macos/host.py), and small shared helpers
for reading that state and asking macOS tools about the machine."""
import json
import os
from pathlib import Path
import subprocess


def state_dir() -> Path:
    return Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))


def read_json(path: Path) -> dict | None:
    """A JSON object from a state file; None when it is missing, unreadable or not an object."""
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Path, value: dict, private=False):
    """Replace a state file in one step, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644), "w") as stream:
        json.dump(value, stream, indent=2)
    os.replace(temporary, path)


def run_quiet(args, timeout=5) -> str:
    """A command's output in the C locale, or "" when it can't run or takes too long."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False,
                              env={**os.environ, "LC_ALL": "C"}).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""
