#!/usr/bin/env python3
"""Read-only preflight for the live React station package and host settings."""
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def validate(package, origin, resolver, station=None):
    package = Path(package).resolve()
    if not re.fullmatch(r"https?://(?:\[[a-fA-F0-9:]+\]|[A-Za-z0-9.-]+)(?::[0-9]{1,5})?", origin):
        raise ValueError("API origin must be http(s)://host[:port], without credentials or a path.")
    parsed = urlsplit(origin)
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Invalid API port.")
    if ipaddress.ip_address(resolver).version != 4:
        raise ValueError("Use an IPv4 LAN DNS resolver.")
    try:
        manifest = json.loads((package / "station-package.json").read_text())
    except (OSError, ValueError) as error:
        raise ValueError("Build the live station package with scripts/build-station.py first.") from error
    if (not isinstance(manifest, dict) or manifest.get("format") != "fabric-conditioning-station" or manifest.get("version") != 1
            or manifest.get("live_api") is not True or manifest.get("offline_transactions") is not False):
        raise ValueError("Expected the live React station package, not the mock prototype.")
    files = manifest.get("files")
    required = {"index.html", "station.html", "station-bootstrap.js", "station-runtime.js"}
    if not isinstance(files, dict) or not required.issubset(files) or not any(path.startswith("assets/") for path in files):
        raise ValueError("Station package is missing its entry point, runtime, or compiled assets.")
    for name, expected in files.items():
        file = package / name
        if (not isinstance(name, str) or Path(name).is_absolute() or ".." in Path(name).parts
                or not file.resolve().is_relative_to(package) or not file.is_file()):
            raise ValueError("Invalid package asset path.")
        if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError("Package checksum failed for " + name + ". Rebuild the station package.")
    station = station or manifest.get("station_id", "shared-door")
    if not isinstance(station, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", station):
        raise ValueError("Station ID must use letters, digits, underscores, or hyphens.")
    return station


if __name__ == "__main__":
    if len(sys.argv) not in {4, 5}:
        raise SystemExit("Usage: validate-package.py UI_DIRECTORY API_ORIGIN DNS_IPV4 [STATION_ID]")
    try:
        print(validate(*sys.argv[1:]))
    except (OSError, ValueError, TypeError) as error:
        raise SystemExit(str(error))
