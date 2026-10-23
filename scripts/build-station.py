#!/usr/bin/env python3
"""Package the compiled, live React app for an independently bootable Pi kiosk."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "fabric-conditioning-station"
VERSION = 1
STATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ROOMS = {"shared-door": ("shared", "Shared conditioning room"),
         "ppe-door": ("ppe", "PPE conditioning room"),
         "footwear-door": ("footwear", "Footwear conditioning room")}


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()
        self.modules = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("type") == "module":
            self.modules += 1
        key = "src" if tag == "script" else "href" if tag == "link" and attrs.get("rel") == "stylesheet" else None
        value = attrs.get(key, "") if key else ""
        if not value:
            return
        if not value.startswith("/assets/") or ".." in Path(value).parts:
            raise ValueError("The station build must use local /assets/ scripts and stylesheets.")
        self.paths.add(value.lstrip("/"))


def build(source: Path, output: Path, station: str = "shared-door") -> dict:
    source, output = source.resolve(), output.resolve()
    if not STATION_PATTERN.fullmatch(station):
        raise ValueError("Station ID must use letters, digits, underscores, or hyphens.")
    if source == output or output in source.parents or source in output.parents:
        raise ValueError("Use a separate output directory outside the source build.")
    index = source / "index.html"
    if not index.is_file() or not (source / "assets").is_dir():
        raise ValueError("Build the React frontend first: cd frontend && npm run build")
    html = index.read_text()
    parser = Assets()
    parser.feed(html)
    if not parser.modules or 'id="root"' not in html:
        raise ValueError("Expected the compiled React index, not a mock station prototype.")
    for path in parser.paths:
        if not (source / path).is_file():
            raise ValueError("Missing compiled asset: " + path)
    if output.exists():
        try:
            existing = json.loads((output / "station-package.json").read_text())
        except (OSError, ValueError) as error:
            raise ValueError("Refusing to replace an output directory without a station-package.json marker.") from error
        if existing.get("format") != FORMAT:
            raise ValueError("Refusing to replace an unrelated output directory.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".station-ui-", dir=output.parent))
    previous = None
    try:
        for item in source.iterdir():
            if item.is_dir():
                shutil.copytree(item, temporary / item.name)
            else:
                shutil.copy2(item, temporary / item.name)
        deployment = ROOT / "deploy/scanner-kiosk"
        for name, target in (("bootstrap.js", "station-bootstrap.js"), ("runtime.js", "station-runtime.js")):
            shutil.copy2(deployment / name, temporary / target)
        room_id, room_name = ROOMS.get(station, (station, "Conditioning station " + station))
        (temporary / "station-config.js").write_text(
            "window.fabricStationConfig = " + json.dumps({"stationId": station, "stationName": room_name + " door",
                                                          "roomId": room_id, "roomName": room_name, "fixed": False,
                                                          "targetWidth": 1280, "targetHeight": 720}) + ";\n")
        injection = ('<script src="/station-config.js"></script>\n'
                     '<script src="/station-bootstrap.js"></script>\n')
        script = re.search(r"<script\b", html, flags=re.I)
        html = html[:script.start()] + injection + html[script.start():]
        html = re.sub(r"</body\s*>", '<script src="/station-runtime.js"></script></body>', html, count=1, flags=re.I)
        for name in ("index.html", "station.html"):
            (temporary / name).write_text(html)
        files = {str(path.relative_to(temporary)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(temporary.rglob("*")) if path.is_file() and path.name != "station-config.js"}
        manifest = {"format": FORMAT, "version": VERSION,
                    "built_at": datetime.now(timezone.utc).isoformat(), "station_id": station,
                    "entrypoint": "/station.html?station=" + station + "#kiosk",
                    "api_prefix": "/api", "target_display": {"width": 1280, "height": 720},
                    "live_api": True, "offline_transactions": False, "files": files}
        (temporary / "station-package.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            previous = Path(tempfile.mkdtemp(prefix=".station-ui-previous-", dir=output.parent))
            previous.rmdir()
            output.rename(previous)
        temporary.rename(output)
        if previous:
            shutil.rmtree(previous)
        return manifest
    except Exception:
        if previous and previous.exists() and not output.exists():
            previous.rename(output)
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "frontend/dist")
    parser.add_argument("--output", type=Path, default=ROOT / "output/station-ui")
    parser.add_argument("--station", default="shared-door")
    args = parser.parse_args()
    try:
        manifest = build(args.source, args.output, args.station)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("Live station package: " + str(args.output.resolve()))
    print("Entry point: " + manifest["entrypoint"])
    print("All frontend assets are local; /api is supplied by the Pi nginx proxy.")


if __name__ == "__main__":
    main()
