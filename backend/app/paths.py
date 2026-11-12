"""Where the Mac host keeps its settings and state (set by deploy/macos/host.py)."""
import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("FABRIC_STATE_DIR", str(Path.home() / "Library/Application Support/Fabric Conditioning")))
