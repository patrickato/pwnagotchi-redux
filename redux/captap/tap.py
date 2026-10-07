"""Capture tap — routes raw 802.11 frames to the consumers that were dark without them.

Feed it monitor-mode frames and it does two jobs:
  - **probe requests → the fingerprint Dex.** It accumulates each station's PNL (the
    SSIDs it directed-probes for) and its IE fingerprint, then builds
    `DeviceObservation`s so the existing linker re-identifies a device **across MAC
    randomization** with REAL data — the input the fingerprint layer was waiting for.
  - **deauth / disassoc → the flood detectors.** It collects normalized `DeauthEvent`s
    (src/dst/bssid/reason/ts) ready for deauth-flood / surveillance-sweep. Those
    detectors live in another build lane, so this stages the events for them rather
    than reaching into that lane.

Pure/testable from raw bytes. The live monitor source (an AF_PACKET socket on a
monitor-mode interface) is honest about tool/hardware absence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .dot11 import parse_dot11, Dot11Frame
from ..dex.fingerprint import DeviceObservation, DeviceLinker


@dataclass(frozen=True)
class DeauthEvent:
    src: str
    dst: str
    bssid: str
    reason: Optional[int]
    ts: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class CaptureTap:
    """Accumulates per-station PNL + IE fingerprint from probe requests, and a list
    of deauth/disassoc events. Deterministic; no I/O."""
    _pnl: Dict[str, Set[str]] = field(default_factory=dict)
    _ie: Dict[str, str] = field(default_factory=dict)
    _first: Dict[str, float] = field(default_factory=dict)
    _last: Dict[str, float] = field(default_factory=dict)
    deauths: List[DeauthEvent] = field(default_factory=list)
    frames_seen: int = 0

    def feed(self, buf: bytes, *, radiotap: bool = False, ts: float = 0.0) -> Optional[Dot11Frame]:
        f = parse_dot11(buf, radiotap=radiotap, ts=ts)
        if f is None:
            return None
        self.frames_seen += 1
        if f.kind == "probe_req":
            s = f.src
            if f.ssid:
                self._pnl.setdefault(s, set()).add(f.ssid)
            else:
                self._pnl.setdefault(s, set())
            if f.ie_hash:
                self._ie[s] = f.ie_hash
            self._first.setdefault(s, ts)
            self._last[s] = ts
        elif f.kind in ("deauth", "disassoc"):
            self.deauths.append(DeauthEvent(f.src, f.dst, f.bssid, f.reason, ts))
        return f

    def observations(self) -> List[DeviceObservation]:
        """One DeviceObservation per station seen — real PNL + IE fingerprint."""
        out = []
        for mac, pnl in self._pnl.items():
            out.append(DeviceObservation(
                mac=mac, ssids=frozenset(pnl), ie_hash=self._ie.get(mac, ""),
                ts=self._last.get(mac, 0.0), first_seen=self._first.get(mac)))
        return out

    def link(self) -> DeviceLinker:
        """Run the fingerprint linker over the captured observations → device
        identities re-identified across MAC randomization."""
        linker = DeviceLinker()
        for obs in self.observations():
            linker.observe(obs)
        return linker

    def deauth_events(self) -> List[dict]:
        """Normalized deauth/disassoc events for the flood detectors (other lane)."""
        return [d.to_dict() for d in self.deauths]


def live_source(iface: str, *, _socket=None):
    """A generator of raw frames from a monitor-mode interface (AF_PACKET).

    NEEDS-HARDWARE: requires a real monitor-mode interface and raw-socket
    privileges. Honest about absence — if a raw socket can't be opened it raises a
    clear error rather than yielding fabricated frames. The parser/tap above do not
    depend on this; they take bytes from any source."""
    import socket as _s
    sock = _socket
    if sock is None:
        try:
            sock = _s.socket(_s.AF_PACKET, _s.SOCK_RAW, _s.ntohs(0x0003))
            sock.bind((iface, 0))
        except (AttributeError, OSError) as e:
            raise RuntimeError(
                f"monitor-mode capture unavailable on '{iface}': {e} "
                "(needs a monitor interface + raw-socket privileges)") from e
    while True:
        import time as _t
        raw = sock.recv(4096)
        yield raw, _t.time()
