"""The only network client. It refuses any host that is not loopback, link-local or private."""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.request
from urllib.parse import urlparse


class NotLocal(Exception):
    pass


def is_local_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return True


def check_url(url: str) -> None:
    host = urlparse(url).hostname or ""
    if not is_local_host(host):
        raise NotLocal(f"refusing non-local host {host!r}: Journeyman only talks to this machine or the local link")


def request_json(url: str, body: dict | None = None, timeout: float = 120.0) -> dict:
    check_url(url)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def internet_reachable(timeout: float = 2.0) -> bool:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout).close()
        return True
    except OSError:
        return False
