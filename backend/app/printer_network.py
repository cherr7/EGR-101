"""USB-to-Wi-Fi fallback for the label printer, and finding the Wi-Fi printer when its address changes.

The USB and Wi-Fi queues are the same Brother printer. If the USB one isn't connected and the Wi-Fi one
answers, a print job goes to Wi-Fi. Every 10 minutes, if the Wi-Fi queue doesn't answer, the Mac looks
for the printer on the network by Bonjour (matching the UUID the printer reports over USB) and, failing
that, in the Mac's ARP table, and points the Wi-Fi queue at the new address.
"""
from __future__ import annotations

import logging
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .paths import read_json, run_quiet, state_dir, write_json

USB_SCHEMES = {"usb", "ippusb"}
FIXABLE_SCHEMES = {"ipp": 631, "ipps": 631, "socket": 9100, "lpd": 515, "http": 631, "https": 631}
CHECK_EVERY = 600
UUID = re.compile(r"uuid=([0-9a-fA-F-]{36})", re.I)
TXT_UUID = re.compile(r"\bUUID=([0-9a-fA-F-]{36})", re.I)
REACHED = re.compile(r"can be reached at (\S+?)\.?:(\d+)")
log = logging.getLogger(__name__)


_run = run_quiet


def devices(lpstat: str | None = None) -> dict[str, str]:
    """Queue name -> device URI, from lpstat -v."""
    lpstat = lpstat or shutil.which("lpstat")
    found = {}
    for line in _run([lpstat, "-v"]).splitlines() if lpstat else []:
        match = re.match(r"device for (\S+?):\s+(\S+)", line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def kind(uri: str | None) -> str | None:
    scheme = urlsplit(uri or "").scheme
    return "usb" if scheme in USB_SCHEMES else "network" if scheme else None


def model(uri: str) -> str | None:
    found = re.search(r"(QL-?\d+\w*)", unquote(uri), re.I)
    return found.group(1).upper() if found else None


def usb_connected(uri: str, listing: str | None = None) -> bool | None:
    """Is this USB printer plugged in? None when it can't be told."""
    name = model(uri)
    if not name:
        return None
    listing = listing if listing is not None else _run(["/usr/sbin/ioreg", "-p", "IOUSB", "-w0"])
    if not listing:
        return None
    return name.replace("-", "").lower() in listing.replace("-", "").lower()


def address(uri: str) -> tuple[str, int] | None:
    parts = urlsplit(uri)
    if parts.scheme not in FIXABLE_SCHEMES or not parts.hostname:
        return None
    return parts.hostname, parts.port or FIXABLE_SCHEMES[parts.scheme]


def reachable(uri: str, timeout=2.0) -> bool:
    target = address(uri)
    if not target:
        return True   # Bonjour (dnssd://) and other URIs find the printer themselves
    try:
        with socket.create_connection(target, timeout=timeout):
            return True
    except OSError:
        return False


def preferences() -> dict:
    return read_json(state_dir() / "printer.json") or {}


def partner(queue: str, uris: dict[str, str]) -> str | None:
    """The other queue for the same label printer: printer.json fallback_queue, else the one queue of the
    other connection type whose name starts with this queue's name (Brother_QL_810W -> Brother_QL_810W_WiFi)."""
    configured = preferences().get("fallback_queue")
    if configured in uris and configured != queue:
        return configured
    mine = kind(uris.get(queue))
    candidates = [name for name, uri in uris.items() if name != queue and kind(uri) not in {None, mine}
                  and (name.startswith(queue) or queue.startswith(name))]
    return candidates[0] if len(candidates) == 1 else None


def usable(queue: dict, uri: str | None, listing: str | None) -> bool:
    """Can a job print here now? Checks the device itself: CUPS keeps an 'offline' alert until the next job."""
    if queue.get("state") == "stopped":
        return False
    if kind(uri) == "usb":
        connected = usb_connected(uri, listing)
        return connected if connected is not None else "offline" not in queue.get("alerts", [])
    if kind(uri) == "network":
        return reachable(uri)
    return "offline" not in queue.get("alerts", [])


def working_queue(requested: str, printers: list[dict], uris: dict[str, str]) -> str | None:
    """The queue a job would print on now: the requested one, else its USB/Wi-Fi partner. None if neither works."""
    by_name = {item["name"]: item for item in printers}
    if requested not in by_name or requested not in uris:
        return None
    other = partner(requested, uris)
    usb = "usb" in {kind(uris[requested]), kind(uris.get(other))}
    listing = _run(["/usr/sbin/ioreg", "-p", "IOUSB", "-w0"]) if usb else None   # once for both queues
    if usable(by_name[requested], uris[requested], listing):
        return requested
    if other in by_name and usable(by_name[other], uris.get(other), listing):
        return other
    return None


def choose_queue(requested: str, printers: list[dict], uris: dict[str, str] | None = None) -> tuple[str, str | None]:
    """The queue to print on, and a note when it isn't the one asked for."""
    uris = devices() if uris is None else uris
    chosen = working_queue(requested, printers, uris)
    if chosen and chosen != requested:
        how = "USB" if kind(uris[requested]) == "usb" else "Wi-Fi"
        return chosen, f"{how} printer not connected. Sent to {chosen}."
    return requested, None


# ---------- finding the Wi-Fi printer ----------

def browse(seconds=4.0) -> list[str]:
    """Bonjour IPP printer names on the local network."""
    names = []
    try:
        process = subprocess.Popen(["/usr/bin/dns-sd", "-B", "_ipp._tcp", "local."], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return names
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    for line in output.splitlines():
        match = re.search(r"\sAdd\s+\d+\s+\d+\s+\S+\s+_ipp\._tcp\.\s+(.+)$", line)
        if match:
            names.append(match.group(1).strip())
    return list(dict.fromkeys(names))


def lookup(name: str, seconds=3.0) -> tuple[str | None, str | None]:
    """Host name and UUID one Bonjour printer reports."""
    try:
        process = subprocess.Popen(["/usr/bin/dns-sd", "-L", name, "_ipp._tcp", "local."], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None, None
    try:
        output, _ = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate()
    host, uuid = REACHED.search(output), TXT_UUID.search(output)
    return (host.group(1) if host else None), (uuid.group(1).lower() if uuid else None)


def resolve(host: str, seconds=3.0) -> str | None:
    result: list[str] = []

    def work():
        try:
            result.append(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])
        except OSError:
            pass
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(seconds)
    return result[0] if result else None


def arp_lookup(mac: str, table: str | None = None) -> str | None:
    """The IPv4 address the Mac last saw for this hardware address."""
    table = table if table is not None else _run(["/usr/sbin/arp", "-an"])
    wanted = mac.lower().replace(":", "").replace("-", "")
    for line in table.splitlines():
        match = re.search(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]+)", line, re.I)
        if match and "".join(part.zfill(2) for part in match.group(2).split(":")).lower() == wanted:
            return match.group(1)
    return None


def find_printer(uuid: str | None, model_name: str | None) -> str | None:
    """The printer's current IPv4 address: Bonjour by UUID (or by model, if only one matches), then ARP."""
    matches = []
    for name in browse():
        if model_name and model_name.replace("-", "").lower() not in name.replace("-", "").lower():
            continue
        host, found_uuid = lookup(name)
        if host and (uuid is None or found_uuid == uuid):
            matches.append(host)
    if len(matches) == 1 or (uuid and matches):
        found = resolve(matches[0])
        if found:
            return found
    if uuid and re.fullmatch(r"[0-9a-f-]{24}[0-9a-f]{12}", uuid):
        return arp_lookup(uuid[-12:])   # Brother UUIDs end with the printer's hardware address
    return None


def with_host(uri: str, ip: str) -> str:
    parts = urlsplit(uri)
    netloc = ip + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=netloc).geturl()


def refresh_wifi_queue() -> dict:
    """Point the Wi-Fi queue at the printer's current address if it moved. Returns what happened."""
    uris = devices()
    usb = [name for name, uri in uris.items() if kind(uri) == "usb" and model(uri)]
    result: dict = {"at": time.time(), "result": "nothing to do"}
    for queue in usb:
        wifi = partner(queue, uris)
        if not wifi or not address(uris[wifi]):
            continue
        result["queue"] = wifi
        if reachable(uris[wifi]):
            result["result"] = "reachable"
            break
        uuid_match = UUID.search(uris[queue])
        ip = find_printer(uuid_match.group(1).lower() if uuid_match else None, model(uris[queue]))
        if not ip:
            result["result"] = "not found"
            break
        if ip == address(uris[wifi])[0]:
            result.update(result="not answering", ip=ip)
            break
        new_uri = with_host(uris[wifi], ip)
        completed = subprocess.run([shutil.which("lpadmin") or "/usr/sbin/lpadmin", "-p", wifi, "-v", new_uri],
                                   capture_output=True, text=True, timeout=20, check=False)
        result.update(ip=ip, result="moved" if completed.returncode == 0 else "found",
                      error=(completed.stderr or "").strip()[:300] or None)
        log.info("Wi-Fi printer %s found at %s (%s)", wifi, ip, result["result"])
        break
    write_status(result)
    return result


def status_path() -> Path:
    return state_dir() / "printer-network.json"


def write_status(result: dict):
    try:
        write_json(status_path(), result)
    except OSError:
        pass


def last_status() -> dict | None:
    return read_json(status_path())


_WATCHER: dict = {}


def ensure_watcher():
    """Check the Wi-Fi printer every 10 minutes in the background (only on the Mac host)."""
    if _WATCHER.get("thread") or not shutil.which("lpstat") or not Path("/usr/bin/dns-sd").exists():
        return

    def loop():
        while True:
            try:
                refresh_wifi_queue()
            except Exception as error:   # never let this stop the server
                log.warning("Wi-Fi printer check failed: %s", error)
            time.sleep(CHECK_EVERY)
    _WATCHER["thread"] = threading.Thread(target=loop, daemon=True, name="wifi-printer")
    _WATCHER["thread"].start()
