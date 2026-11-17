"""
Source-link reachability + SSRF guard (ports of `web/lib/check-link.ts`, `web/lib/net-guard.ts` and
`recheckAllLinks` / `applyLinkStatus` from `modules/records/queries.ts`).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests

import constants as C
from utils import db, queries
from utils.db import table

log = logging.getLogger(__name__)

TIMEOUT = C.LINK_CHECK_TIMEOUT
MAX_REDIRECTS = C.LINK_CHECK_MAX_REDIRECTS
_HEADERS = {"user-agent": C.LINK_CHECK_USER_AGENT}


def is_private_ip(ip: str) -> bool:
    """Private / loopback / link-local / CGNAT / cloud-metadata addresses are refused."""
    try:
        addr = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv4Address):
        a, b = addr.packed[0], addr.packed[1]
        if a in (0, 10, 127) or (a == 169 and b == 254) or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
            return True
        return a == 100 and 64 <= b <= 127
    return addr.is_loopback or addr.is_unspecified or addr.is_link_local or addr.is_private


def assert_public_host(hostname: str) -> None:
    if not hostname:
        raise ValueError("Missing host")
    try:
        ipaddress.ip_address(hostname)
        if is_private_ip(hostname):
            raise ValueError("Blocked private address")
        return
    except ValueError as exc:
        if "Blocked" in str(exc):
            raise
    for info in socket.getaddrinfo(hostname, None):
        if is_private_ip(info[4][0]):
            raise ValueError(f"Blocked non-public address for {hostname}")


def check_link(raw_url: str | None) -> bool:
    """True if the URL answers 2xx/3xx within the timeout (HEAD first, GET fallback; redirects re-checked)."""
    if not raw_url or not raw_url.strip():
        return False
    current = raw_url.strip()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            u = urlparse(current)
            if u.scheme not in ("http", "https"):
                return False
            assert_public_host(u.hostname or "")
            try:
                res = requests.head(current, allow_redirects=False, timeout=TIMEOUT, headers=_HEADERS)
            except requests.RequestException:
                res = requests.get(current, allow_redirects=False, timeout=TIMEOUT, headers=_HEADERS, stream=True)
            if res.status_code in (405, 501):
                res = requests.get(current, allow_redirects=False, timeout=TIMEOUT, headers=_HEADERS, stream=True)
            if 300 <= res.status_code < 400:
                loc = res.headers.get("location")
                if not loc:
                    return False
                current = urljoin(current, loc)
                continue
            return 200 <= res.status_code < 400
        return False
    except Exception:  # noqa: BLE001 - any error / timeout / private host = “not working”
        return False


def apply_link_status(record_id: str, link: str | None) -> None:
    """Check one record's link and store the result (broken keeps the previous last-working time)."""
    if not link or not link.strip():
        queries.set_link_status(record_id, None, None, None)
        return
    ok = check_link(link)
    now = db.now()
    queries.set_link_status(record_id, 1 if ok else 0, now, now if ok else "keep")


def recheck_all(progress: Callable[[int, int], None] | None = None) -> dict:
    recs = db.query(f"SELECT id, link FROM {table('policy_records')} WHERE link IS NOT NULL AND link <> ''")
    working = broken = 0
    done = 0

    def one(r: dict) -> bool:
        apply_link_status(r["id"], r["link"])
        return bool(queries.db.query(f"SELECT link_ok FROM {table('policy_records')} WHERE id = :i", {"i": r["id"]})[0]["link_ok"] == 1)

    with ThreadPoolExecutor(max_workers=6) as pool:
        for ok in pool.map(one, recs):
            working += 1 if ok else 0
            broken += 0 if ok else 1
            done += 1
            if progress:
                progress(done, len(recs))
    db.invalidate("")
    return {"checked": len(recs), "working": working, "broken": broken}
