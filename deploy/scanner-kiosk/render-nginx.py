#!/usr/bin/env python3
"""Write the station's nginx site from nginx.conf.template.

  render-nginx.py TEMPLATE API_ORIGIN DNS_IPV4 [FALLBACK_ORIGIN]
  render-nginx.py --check-fallback FALLBACK_ORIGIN

With a fallback (http://IP:PORT on the local network), /api tries the Tailscale address first,
then the fallback. A request that may have reached the Mac is never re-sent.
"""
import ipaddress
import socket
import sys
from urllib.parse import urlsplit

UPSTREAM = "fabric_mac"


def fallback_address(origin):
    parts = urlsplit(origin)
    if parts.scheme != "http" or parts.path not in ("", "/") or parts.query or parts.username:
        raise ValueError("API_FALLBACK must be http://IP:PORT.")
    try:
        ip = ipaddress.ip_address(parts.hostname or "")
    except ValueError:
        raise ValueError("API_FALLBACK must use an IPv4 address, not a name.") from None
    if ip.version != 4:
        raise ValueError("API_FALLBACK must use an IPv4 address.")
    return f"{ip}:{parts.port or 80}"


def primary_address(origin):
    parts = urlsplit(origin)
    if parts.scheme != "http":
        raise ValueError("The main API origin must be http:// to use a fallback.")
    host, port = parts.hostname or "", parts.port or 80
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0])
        except OSError:
            raise ValueError(f"This Pi cannot resolve {host}. Is it on the tailnet?") from None
    return f"{ip}:{port}"


def render(template, origin, resolver, fallback=""):
    if not fallback:
        return template.replace("__API_ORIGIN__", origin).replace("__DNS_RESOLVER__", resolver)
    main, backup = primary_address(origin), fallback_address(fallback)
    servers = [f"    server {main} max_fails=1 fail_timeout=30s;  # {urlsplit(origin).hostname}"]
    if backup != main:
        servers.append(f"    server {backup} backup;  # local network")
    head = f"upstream {UPSTREAM} {{\n" + "\n".join(servers) + "\n}\n\n"
    return head + template.replace("__API_ORIGIN__", "http://" + UPSTREAM).replace("__DNS_RESOLVER__", resolver)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--check-fallback":
            fallback_address(sys.argv[2])
        elif len(sys.argv) in (4, 5):
            with open(sys.argv[1]) as file:
                sys.stdout.write(render(file.read(), *sys.argv[2:]))
        else:
            raise SystemExit(__doc__)
    except ValueError as error:
        raise SystemExit(str(error))
