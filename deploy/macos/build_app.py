#!/usr/bin/env python3
"""Create a self-contained code bundle; database/runtime remain outside the app."""
import argparse
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.version import stamp as version_stamp, write as version_write  # noqa: E402

# macOS cannot tell the CPU architecture of a script-only bundle executable, so on
# Apple Silicon it treats the app as Intel and asks for Rosetta. A tiny native
# (arm64 + x86_64) executable avoids that; it only hands over to launch.zsh.
NATIVE_STUB = r"""
#include <limits.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char *argv[]) {
    char path[PATH_MAX], real[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size) != 0 || !realpath(path, real)) {
        fprintf(stderr, "Fabric Conditioning: cannot locate the app bundle.\n");
        return 1;
    }
    snprintf(script, sizeof(script), "%s/../Resources/launch.zsh", dirname(real));
    char **args = calloc((size_t)argc + 2, sizeof(char *));
    if (!args) return 1;
    args[0] = "/bin/zsh";
    args[1] = script;
    for (int i = 1; i < argc; i++) args[i + 1] = argv[i];
    execv("/bin/zsh", args);
    perror("Fabric Conditioning: /bin/zsh");
    return 1;
}
"""

LAUNCH_SCRIPT = """#!/bin/zsh
set -e
APP_ROOT="$(cd "$(dirname "$0")/app" && pwd)"
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
"""


def compile_native_launcher(target):
    """Build a universal launcher with the Command Line Tools' clang."""
    clang = shutil.which("clang")
    if not clang:
        raise RuntimeError("clang is required to build the app launcher: run xcode-select --install")
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "launcher.c"
        source.write_text(NATIVE_STUB)
        subprocess.run([clang, "-O2", "-arch", "arm64", "-arch", "x86_64",
                        "-mmacosx-version-min=13.0", "-o", str(target), str(source)], check=True)
    # Ad-hoc signature so Gatekeeper/arm64 accept the freshly linked binary.
    subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    facts = version_stamp(ROOT)
    version_write(contents / "Resources/app", facts)
    info = {"CFBundleName": "Fabric Conditioning", "CFBundleDisplayName": "Fabric Conditioning",
            "CFBundleIdentifier": "org.egr.fabric-conditioning", "CFBundleVersion": "3",
            "CFBundleShortVersionString": facts["version"].lstrip("v"), "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "Fabric Conditioning", "LSUIElement": True,
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "LSArchitecturePriority": ["arm64", "x86_64"]}
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    script = contents / "Resources/launch.zsh"
    script.write_text(LAUNCH_SCRIPT)
    script.chmod(0o755)
    launcher = contents / "MacOS/Fabric Conditioning"
    if launcher.exists():
        launcher.unlink()
    compile_native_launcher(launcher)
    (contents / "PkgInfo").write_text("APPL????")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "output/macOS/Fabric Conditioning.app")
    args = parser.parse_args()
    print(build(args.output.resolve()))
