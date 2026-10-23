"""Is the device actually online right now? (glass-box, injected probe)

Everything in redux that could legitimately leave the device — an opt-in
WiGLE/wpa-sec upload, an OTA pull, a phone-PAN sync — should fire only when there
is real uplink, and never uninvited. This is the one honest "do I have internet"
check they can share.

It answers a question; it transmits no capture data. The reachability probe is
injected, so the logic is fully testable with no network, and the default probe
is a bare TCP connect to a public resolver (reveals nothing, standard online
check). Callers still gate the actual upload behind their own opt-in flag — being
online is necessary, never sufficient.
"""
from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable, Optional

# A callable(host, port, timeout) -> bool used to test reachability. Injected in
# tests; the default does a real TCP connect.
Probe = Callable[[str, int, float], bool]


@dataclass(frozen=True)
class OnlineStatus:
    online: bool
    reason: str          # human-readable, glass-box
    via: str = ""        # how it was determined (host:port, or an error tag)


def _default_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_online(*, probe: Optional[Probe] = None, host: str = "one.one.one.one",
                 port: int = 443, timeout: float = 2.0) -> OnlineStatus:
    """Return a glass-box OnlineStatus. `probe` is injected for tests; the default
    TCP-connects to a public resolver. Any probe error is reported as offline
    (fail-closed) rather than raised."""
    probe = probe or _default_probe
    where = f"{host}:{port}"
    try:
        reachable = bool(probe(host, port, timeout))
    except Exception as e:  # an injected/odd probe must not crash the caller
        return OnlineStatus(False, f"offline: reachability probe errored ({e})", via="probe-error")
    if reachable:
        return OnlineStatus(True, f"online: reached {where}", via=where)
    return OnlineStatus(False, f"offline: could not reach {where}", via=where)
