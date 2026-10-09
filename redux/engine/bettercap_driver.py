"""Bettercap driver — talk to bettercap directly over its REST API.

This is the engine that replaces pwnagotchi's supervisor role: drive bettercap
(the capture engine) with no pwnagotchi in the path. pwnagotchi drove bettercap
for only wifi.recon/deauth/assoc + handshake pcap; this driver is built to expose
the whole surface (atlas B-1: the event stream as Augur's nervous system).

Design for testability: all I/O goes through a `Transport` (a tiny protocol:
`run(cmd)`, `session()`, `events()`). The live transport (`HttpTransport`) is
stdlib urllib + basic auth. A `ReplayTransport` feeds recorded JSON so suites can
be exercised in CI with no radio (atlas B-12). The driver itself holds zero
sockets, so every method here is unit-tested hardware-free.

Scope (AGENTS.md): capture/recon on authorized/own networks is passive and open.
Any firing capability (deauth/assoc) is gated on an explicit authorized-target
allowlist, **empty by default** — the gate is a real code path (`FiringRefused`),
not a comment. Every action carries a human-readable `reason` (glass-box).

Target platform: arm64 Raspberry Pi 4 / Pi 5, jayofelony bettercap base.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

_BIND_SCOPES = ("localhost", "tailscale", "lan", "auto")


@dataclass
class BettercapConfig:
    host: str = "127.0.0.1"
    port: int = 8081
    scheme: str = "http"
    username: str = "user"
    password: str = "pass"
    # bind_scope governs exposure of any listener this drives; default least-exposed.
    bind_scope: str = "localhost"  # localhost | tailscale | lan | auto

    def __post_init__(self):
        if self.bind_scope not in _BIND_SCOPES:
            raise ValueError(f"bind_scope must be one of {_BIND_SCOPES}, got {self.bind_scope!r}")

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}/api"


# --------------------------------------------------------------------------- #
# Authorized-target allowlist (the firing gate)
# --------------------------------------------------------------------------- #

def _norm_bssid(b: str) -> str:
    return b.strip().lower()


@dataclass
class Allowlist:
    """Explicit authorized targets. EMPTY BY DEFAULT — nothing is permitted to be
    fired at until the operator adds it. Matches on BSSID or SSID."""
    bssids: set = field(default_factory=set)
    ssids: set = field(default_factory=set)

    def __post_init__(self):
        self.bssids = {_norm_bssid(b) for b in self.bssids}
        self.ssids = set(self.ssids)

    @property
    def empty(self) -> bool:
        return not self.bssids and not self.ssids

    def permits(self, bssid: str | None = None, ssid: str | None = None) -> bool:
        if bssid is not None and _norm_bssid(bssid) in self.bssids:
            return True
        if ssid is not None and ssid in self.ssids:
            return True
        return False

    def add(self, bssid: str | None = None, ssid: str | None = None) -> None:
        if bssid:
            self.bssids.add(_norm_bssid(bssid))
        if ssid:
            self.ssids.add(ssid)


class FiringRefused(Exception):
    """Raised when a firing-capable action targets something not on the allowlist.
    Carries the human-readable reason it was refused (glass-box)."""


# --------------------------------------------------------------------------- #
# Normalized events (glass-box)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Event:
    type: str          # normalized: ap.new | client.new | handshake | ble.new | ...
    at: float          # epoch seconds
    data: dict
    reason: str        # one-line human-readable "why this matters"
    raw_tag: str = ""  # original bettercap tag


# bettercap tag -> (normalized type, reason template)
_EVENT_MAP = {
    "wifi.ap.new": ("ap.new", "new access point seen"),
    "wifi.ap.lost": ("ap.lost", "access point went away"),
    "wifi.client.new": ("client.new", "new client seen"),
    "wifi.client.handshake": ("handshake", "captured a handshake/PMKID"),
    "ble.device.new": ("ble.new", "new BLE device seen"),
    "ble.device.lost": ("ble.lost", "BLE device went away"),
}


def normalize_event(raw: dict) -> Event:
    """Map one raw bettercap event dict into a normalized glass-box Event."""
    tag = raw.get("tag", "")
    data = raw.get("data", {}) or {}
    norm, reason = _EVENT_MAP.get(tag, (tag or "unknown", "event"))
    # enrich the reason with a concrete detail where we can, without inventing data
    if norm == "handshake" and isinstance(data, dict):
        ap = data.get("ap") or data.get("station") or ""
        if ap:
            reason = f"captured a handshake from {ap}"
    elif norm in ("ap.new", "ap.lost") and isinstance(data, dict):
        name = data.get("hostname") or data.get("essid") or data.get("mac") or ""
        if name:
            reason = f"{reason}: {name}"
    at = _parse_time(raw.get("time"))
    return Event(type=norm, at=at, data=data, reason=reason, raw_tag=tag)


def _parse_time(t) -> float:
    if isinstance(t, (int, float)):
        return float(t)
    # bettercap emits RFC3339 strings; fall back to now if unparseable (don't crash)
    return time.time()


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

class Transport(Protocol):
    def run(self, cmd: str) -> dict: ...
    def session(self) -> dict: ...
    def events(self, clear: bool = False) -> list: ...


class HttpTransport:
    """Live transport: bettercap REST over stdlib urllib + basic auth.

    Needs a running bettercap with its REST API up. Network errors are surfaced
    as BettercapUnavailable rather than raw urllib errors, so the supervisor can
    react instead of crashing.
    """

    def __init__(self, config: BettercapConfig, timeout: float = 5.0):
        self.config = config
        self.timeout = timeout
        token = f"{config.username}:{config.password}".encode()
        self._auth = "Basic " + base64.b64encode(token).decode()

    def _request(self, method: str, path: str, body: dict | None = None):
        url = f"{self.config.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", self._auth)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                return json.loads(payload) if payload else {}
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise BettercapUnavailable(f"bettercap API unreachable at {url}: {e}") from e

    def run(self, cmd: str) -> dict:
        return self._request("POST", "/session", {"cmd": cmd}) or {}

    def session(self) -> dict:
        return self._request("GET", "/session") or {}

    def events(self, clear: bool = False) -> list:
        path = "/events"
        out = self._request("GET", path)
        if not isinstance(out, list):
            raise BettercapUnavailable("bettercap event endpoint returned an unexpected payload")
        if clear:
            # Failing to clear means the next poll would replay stale events
            # as though they were new observations. Do not silently continue.
            self._request("DELETE", "/events")
        return out


class ReplayTransport:
    """Offline transport for tests/CI: replays a recorded event list and records
    the commands it was asked to run. No sockets. (atlas B-12 record/replay.)"""

    def __init__(self, events: list | None = None, session: dict | None = None):
        self._events = list(events or [])
        self._session = session or {}
        self.commands: list = []

    def run(self, cmd: str) -> dict:
        self.commands.append(cmd)
        return {"success": True}

    def session(self) -> dict:
        return dict(self._session)

    def events(self, clear: bool = False) -> list:
        out = list(self._events)
        if clear:
            self._events = []
        return out


class BettercapUnavailable(Exception):
    """bettercap's API could not be reached."""


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

class BettercapDriver:
    """Drives bettercap over an injected `Transport`.

    Passive/recon/capture methods are open. Firing methods (`deauth`) refuse any
    target not on the `allowlist` (empty by default) with a `FiringRefused` that
    explains why. `log` receives one glass-box line per action.
    """

    def __init__(
        self,
        config: BettercapConfig | None = None,
        transport: Transport | None = None,
        allowlist: Allowlist | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.config = config or BettercapConfig()
        self.transport = transport or HttpTransport(self.config)
        self.allowlist = allowlist or Allowlist()
        self._log = log or (lambda msg: None)

    @property
    def base_url(self) -> str:
        return self.config.base_url

    def _say(self, reason: str) -> None:
        self._log(reason)

    # --- lifecycle / control (passive, open) ------------------------------- #

    def set_interface(self, iface: str) -> dict:
        """Point bettercap at the interface the Radio Orchestrator chose."""
        self._say(f"bettercap: binding wifi to {iface}")
        return self.transport.run(f"set wifi.interface {iface}")

    def recon(self, on: bool = True) -> dict:
        self._say(f"bettercap: wifi.recon {'on' if on else 'off'}")
        return self.transport.run(f"wifi.recon {'on' if on else 'off'}")

    def set_channels(self, channels: list) -> dict:
        chans = ",".join(str(c) for c in channels)
        self._say(f"bettercap: restricting recon to channels {chans}")
        return self.transport.run(f"wifi.recon.channel {chans}")

    def set_handshake_file(self, path: str) -> dict:
        self._say(f"bettercap: writing handshakes to {path}")
        return self.transport.run(f"set wifi.handshakes.file {path}")

    def poll_events(self, clear: bool = True) -> list:
        """Fetch + normalize bettercap events into glass-box Events."""
        raw = self.transport.events(clear=clear)
        return [normalize_event(e) for e in raw]

    def stream_events(self, interval: float = 1.0, _max_polls: int | None = None) -> Iterator[Event]:
        """Poll-based event stream. (A websocket transport is the richer path and
        is a labeled live-hardware gate; polling is correct and testable now.)"""
        polls = 0
        while _max_polls is None or polls < _max_polls:
            for ev in self.poll_events(clear=True):
                yield ev
            polls += 1
            if _max_polls is None or polls < _max_polls:
                time.sleep(interval)

    # --- firing (GATED: authorized targets only) --------------------------- #

    def deauth(self, bssid: str, ssid: str | None = None) -> dict:
        """Deauth a target. REFUSES unless the target is on the allowlist
        (empty by default). This is the gate, as real code."""
        if self.allowlist.empty:
            reason = (f"REFUSED deauth of {bssid}: authorized-target allowlist is empty "
                      f"(add the target explicitly to authorize)")
            self._say(reason)
            raise FiringRefused(reason)
        if not self.allowlist.permits(bssid=bssid, ssid=ssid):
            reason = f"REFUSED deauth of {bssid}: not on the authorized-target allowlist"
            self._say(reason)
            raise FiringRefused(reason)
        self._say(f"bettercap: deauth {bssid} (authorized)")
        return self.transport.run(f"wifi.deauth {bssid}")
