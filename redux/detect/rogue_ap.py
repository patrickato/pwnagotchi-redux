"""Rogue-AP / evil-twin detector.

Compares observed beacons (and probe responses) against a trusted network
list. Flags a known SSID advertised from an unexpected BSSID, or with a
mismatched channel / security suite. Pure detection — no TX.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_ADVERT = frozenset({FrameType.BEACON, FrameType.PROBE_RESP})


@dataclass(frozen=True)
class TrustedNetwork:
    """An SSID the operator asserts is legitimate, with optional constraints."""

    ssid: str
    bssids: frozenset  # allowed BSSIDs (lowercase); empty = any BSSID OK for this SSID
    channels: frozenset = frozenset()  # empty = any channel
    security: frozenset = frozenset()  # empty = any; values like {"wpa2-psk"}

    def __post_init__(self) -> None:
        object.__setattr__(self, "ssid", self.ssid)  # keep as-is (SSID is case-sensitive in 802.11)
        object.__setattr__(
            self,
            "bssids",
            frozenset(b.lower() for b in (self.bssids or frozenset())),
        )
        object.__setattr__(
            self,
            "security",
            frozenset(s.lower() for s in (self.security or frozenset())),
        )


class RogueAPDetector:
    """Detect evil-twin / rogue advertisements against a trusted set.

    If the trusted list is empty, this detector stays quiet (nothing to
    compare against) — same philosophy as the empty-by-default allowlist.
    """

    def __init__(self, trusted: Iterable[TrustedNetwork] | None = None) -> None:
        self._by_ssid: Dict[str, TrustedNetwork] = {}
        for t in trusted or ():
            self._by_ssid[t.ssid] = t
        self._seen_alerts: Set[tuple] = set()  # (ssid, bssid, reason-key) de-dupe

    def reset(self) -> None:
        self._seen_alerts.clear()

    def set_trusted(self, trusted: Iterable[TrustedNetwork]) -> None:
        self._by_ssid = {t.ssid: t for t in trusted}
        self._seen_alerts.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _ADVERT:
            return None
        if not frame.ssid:
            return None

        trusted = self._by_ssid.get(frame.ssid)
        if trusted is None:
            return None  # unknown SSID is not "rogue" under this policy

        reasons: List[str] = []
        if trusted.bssids and frame.bssid and frame.bssid not in trusted.bssids:
            reasons.append(
                f"BSSID {frame.bssid} not in trusted set "
                f"({', '.join(sorted(trusted.bssids)) or 'empty'})"
            )
        if trusted.channels and frame.channel is not None and frame.channel not in trusted.channels:
            reasons.append(
                f"channel {frame.channel} not in trusted "
                f"{sorted(trusted.channels)}"
            )
        if trusted.security and frame.security and frame.security not in trusted.security:
            reasons.append(
                f"security '{frame.security}' not in trusted "
                f"{sorted(trusted.security)}"
            )

        if not reasons:
            return None

        key = (frame.ssid, frame.bssid, tuple(reasons))
        if key in self._seen_alerts:
            return None
        self._seen_alerts.add(key)

        reason = (
            f"rogue-AP / evil-twin candidate for SSID '{frame.ssid}': "
            + "; ".join(reasons)
        )
        return Alert(
            kind=AlertKind.ROGUE_AP,
            reason=reason,
            ts=frame.ts,
            severity="critical",
            bssid=frame.bssid,
            ssid=frame.ssid,
            detail={
                "mismatches": list(reasons),
                "channel": frame.channel,
                "security": frame.security,
            },
        )

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out


def parse_trusted_networks(entries) -> List[TrustedNetwork]:
    """Build a trusted-network list from config entries (the `[detect]`
    trusted_networks array): [{ssid, bssids, channels, security}]. The rogue-AP
    detector stays quiet until this is populated, so this is what makes the
    headline evil-twin detector actually fire against your declared networks.
    """
    def _ints(xs):
        out = set()
        for x in xs or ():
            if isinstance(x, bool):
                continue
            if isinstance(x, int):
                out.add(x)
            elif isinstance(x, str) and x.isdigit():
                out.add(int(x))
        return frozenset(out)

    nets: List[TrustedNetwork] = []
    for e in entries or ():
        if not isinstance(e, dict):
            continue
        ssid = e.get("ssid")
        if not isinstance(ssid, str) or not ssid:
            continue
        nets.append(TrustedNetwork(
            ssid=ssid,
            bssids=frozenset(str(b) for b in (e.get("bssids") or ()) if isinstance(b, str)),
            channels=_ints(e.get("channels")),
            security=frozenset(str(s) for s in (e.get("security") or ()) if isinstance(s, str)),
        ))
    return nets
