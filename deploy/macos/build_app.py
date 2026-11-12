"""Build the Mac app: a menu-bar app plus the boot service it registers.

  build_app.py              build output/macOS/Fabric Conditioning.app
  build_app.py --install    build it, put it in /Applications and open it

The app holds no code or data: the service runs the release in
<state>/releases/current (see update.py), or this checkout before the first release.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.version import stamp as version_stamp  # noqa: E402

BUNDLE_ID = "org.egr.fabric-conditioning"
INSTALLED = Path("/Applications/Fabric Conditioning.app")

# launchd starts this small native program, which runs serve.zsh (a script alone can't be a signed service).
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
    snprintf(script, sizeof(script), "%s/../Resources/serve.zsh", dirname(real));
    execl("/bin/zsh", "/bin/zsh", script, (char *)NULL);
    perror("Fabric Conditioning: /bin/zsh");
    return 1;
}
"""

SERVE_SCRIPT = """#!/bin/zsh
# Run by launchd at boot (or at login until the boot service is approved).
STATE="${FABRIC_STATE_DIR:-$HOME/Library/Application Support/Fabric Conditioning}"
ROOT="$STATE/releases/current"
[[ -d "$ROOT" ]] || ROOT=__SOURCE__
export FABRIC_APP_ROOT="$ROOT" FABRIC_STATE_DIR="$STATE" LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$STATE/logs"
exec "$STATE/runtime/bin/python" "$ROOT/deploy/macos/host.py" serve
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
    for name in ["backend/alembic.ini", "backend/requirements.txt", "deploy/macos/host.py"]:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)


def compile_menu_app(target):
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise RuntimeError("swiftc is required to build the app: run xcode-select --install")
    subprocess.run([swiftc, "-O", "-target", ("arm64" if os.uname().machine == "arm64" else "x86_64") + "-apple-macos13.0",
                    "-o", str(target), str(ROOT / "deploy/macos/MenuApp.swift")], check=True)


def service_plist(state, user, home, program):
    log = str(state / "logs/service.log")
    return {"Label": BUNDLE_ID, "UserName": user, "GroupName": "staff",
            "EnvironmentVariables": {"HOME": home, "USER": user, "LANG": "en_US.UTF-8"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
            "StandardOutPath": log, "StandardErrorPath": log, **program}


def build(output, destination=None):
    """destination: where the app will live (for the login fallback's absolute path)."""
    destination = Path(destination or output)
    user, home = getpass.getuser(), str(Path.home())
    state = user_state()
    if output.exists():
        shutil.rmtree(output)
    contents = output / "Contents"
    for folder in ("MacOS", "Resources", "Library/LaunchDaemons"):
        (contents / folder).mkdir(parents=True, exist_ok=True)
    facts = version_stamp(ROOT)
    info = {"CFBundleName": "Fabric Conditioning", "CFBundleDisplayName": "Fabric Conditioning",
            "CFBundleIdentifier": BUNDLE_ID, "CFBundleVersion": "4",
            "CFBundleShortVersionString": facts["version"].lstrip("v").split("-g")[0], "CFBundlePackageType": "APPL",
            "CFBundleExecutable": "Fabric Conditioning", "LSUIElement": True,
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True}
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    (contents / "PkgInfo").write_text("APPL????")
    script = contents / "Resources/serve.zsh"
    script.write_text(SERVE_SCRIPT.replace("__SOURCE__", "'" + str(ROOT).replace("'", "'\\''") + "'"))
    script.chmod(0o755)
    compile_native_launcher(contents / "MacOS/fabric-service")
    compile_menu_app(contents / "MacOS/Fabric Conditioning")
    with (contents / "Library/LaunchDaemons" / (BUNDLE_ID + ".plist")).open("wb") as stream:
        plistlib.dump(service_plist(state, user, home, {"BundleProgram": "Contents/MacOS/fabric-service",
                                                        "AssociatedBundleIdentifiers": [BUNDLE_ID]}), stream)
    login = service_plist(state, user, home, {"ProgramArguments": [str(destination / "Contents/MacOS/fabric-service")]})
    login["Label"] = BUNDLE_ID + ".login"
    for key in ("UserName", "GroupName"):
        login.pop(key)
    with (contents / "Resources/login-service.plist").open("wb") as stream:
        plistlib.dump(login, stream)
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(output)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return output


def install(built):
    """Replace /Applications/Fabric Conditioning.app (only if it is this app) and open it."""
    if INSTALLED.exists():
        try:
            with (INSTALLED / "Contents/Info.plist").open("rb") as stream:
                if plistlib.load(stream).get("CFBundleIdentifier") != BUNDLE_ID:
                    raise RuntimeError(f"{INSTALLED} is a different app. Move it away first.")
        except OSError:
            raise RuntimeError(f"{INSTALLED} exists and isn't this app. Move it away first.") from None
        subprocess.run(["/usr/bin/pkill", "-x", "Fabric Conditioning"], check=False)
        shutil.rmtree(INSTALLED)
    shutil.copytree(built, INSTALLED, symlinks=True)
    remember_repo(user_state())
    subprocess.run(["/usr/bin/open", str(INSTALLED)], check=False)
    return INSTALLED


def user_state():
    return Path(os.environ.get("FABRIC_STATE_DIR", Path.home() / "Library/Application Support/Fabric Conditioning"))


def remember_repo(state):
    """The web app's Update button runs this checkout's updater."""
    path = state / "config.json"
    try:
        facts = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if facts.get("source_repo") != str(ROOT):
        facts["source_repo"] = str(ROOT)
        temporary = path.with_suffix(".tmp")
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as stream:
            json.dump(facts, stream, indent=2)
        os.replace(temporary, path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=ROOT / "output/macOS/Fabric Conditioning.app")
    parser.add_argument("--install", action="store_true", help="put the app in /Applications and open it")
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        built = build(output, INSTALLED if args.install else output)
        print(install(built) if args.install else built)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit("ERROR: " + str(error))
