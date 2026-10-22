#!/usr/bin/env python3
"""Create a self-contained code bundle; database/runtime remain outside the app."""
import argparse
from pathlib import Path
import plistlib
import shutil

ROOT = Path(__file__).resolve().parents[2]


def copy_code(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".pytest_cache")
    for folder in ["backend/app", "backend/alembic", "frontend/dist"]:
        target = destination / folder
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source / folder, target, ignore=ignore)
    for name in ["backend/alembic.ini", "backend/requirements.txt", "deploy/macos/host.py", "deploy/macos/launcher.py"]:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)


def build(output):
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise RuntimeError("Build the frontend first: cd frontend && npm run build")
    contents = output / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    copy_code(ROOT, contents / "Resources/app")
    info = {"CFBundleName": "Fabric Conditioning", "CFBundleDisplayName": "Fabric Conditioning",
            "CFBundleIdentifier": "org.egr.fabric-conditioning", "CFBundleVersion": "2",
            "CFBundleShortVersionString": "2.0", "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "Fabric Conditioning", "LSUIElement": True,
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True}
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    launcher = contents / "MacOS/Fabric Conditioning"
    launcher.write_text("""#!/bin/zsh
set -e
APP_ROOT="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
STATE="$FABRIC_STATE_DIR"
[[ -n "$STATE" ]] || STATE="$HOME/Library/Application Support/Fabric Conditioning"
if [[ -f "/Library/Application Support/Fabric Conditioning/config.json" && -z "$FABRIC_STATE_DIR" ]]; then
  STATE="/Library/Application Support/Fabric Conditioning"
fi
PREFERRED="$FABRIC_PYTHON"
[[ -n "$PREFERRED" ]] || PREFERRED="$STATE/runtime/bin/python"
for PYTHON in "$PREFERRED" /opt/homebrew/opt/python@3.14/bin/python3.14 /opt/homebrew/bin/python3.14 /usr/local/bin/python3.14 /usr/bin/python3; do
  if [[ -x "$PYTHON" ]]; then
    exec "$PYTHON" "$APP_ROOT/deploy/macos/launcher.py" "$@"
  fi
done
print -u2 "Run deploy/macos/setup.sh to install Python."
exit 1
""")
    launcher.chmod(0o755)
    (contents / "PkgInfo").write_text("APPL????")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output/macOS/Fabric Conditioning.app")
    args = parser.parse_args()
    print(build(args.output.resolve()))
