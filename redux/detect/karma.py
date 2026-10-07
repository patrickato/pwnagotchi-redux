"""Karma / evil-twin captive-portal detector.

Two passive signatures (atlas-style defense, no TX):

1. **Karma** — one BSSID emits probe responses for many distinct SSIDs in a
   short window (answers every probe, classic PineAP/Karma).
2. **Captive twin** — the same SSID is advertised from a second BSSID with
   weaker/open security while another BSSID already showed a stronger suite
   (open lure next to a real WPA network).

Standalone Frame input; pure detection.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Set, Tuple

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_ADVERT = frozenset({FrameType.BEACON, FrameType.PROBE_RESP})

# Relative security strength for twin comparison (higher = stronger).
_SEC_RANK = {
    "open": 0,
    "owe": 1,
    "wep": 2,
    "wpa": 3,
    "wpa-psk": 3,
    "wpa2": 4,
    "wpa2-psk": 4,
    "wpa3": 5,
    "wpa3-sae": 5,
    "wpa3-transition": 4,
    "unknown": -1,
    "": -1,
}


def _rank(security: str) -> int:
    return _SEC_RANK.get((security or "").lower(), -1)


class KarmaCaptiveDetector:
    """Detects Karma probe-response floods and open captive twins."""

    def __init__(
        self,
        *,
        karma_window_s: float = 15.0,
        karma_ssid_threshold: int = 8,
    ) -> None:
        if karma_window_s <= 0:
            raise ValueError("karma_window_s must be positive")
        if karma_ssid_threshold < 1:
            raise ValueError("karma_ssid_threshold must be >= 1")
        self.karma_window_s = karma_window_s
        self.karma_ssid_threshold = karma_ssid_threshold
        # BSSID -> deque of (ts, ssid) for probe_resp only
        self._probe_by_bssid: Dict[str, Deque[Tuple[float, str]]] = defaultdict(deque)
        # SSID -> list of (bssid, security, channel) last seen via beacon/probe_resp
        self._ssid_ads: Dict[str, Dict[str, Tuple[str, Optional[int]]]] = defaultdict(dict)
        self._fired: Set[Tuple[str, str]] = set()  # (kind, key) de-dupe

    def reset(self) -> None:
        self._probe_by_bssid.clear()
        self._ssid_ads.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is FrameType.PROBE_RESP:
            a = self._check_karma(frame)
            if a is not None:
                return a
        if frame.type in _ADVERT and frame.ssid:
            return self._check_captive_twin(frame)
        return None

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out

    def _check_karma(self, frame: Frame) -> Optional[Alert]:
        bssid = frame.bssid
        ssid = frame.ssid
        if not bssid or not ssid:
            return None
        ts = frame.ts
        q = self._probe_by_bssid[bssid]
        q.append((ts, ssid))
        cutoff = ts - self.karma_window_s
        while q and q[0][0] < cutoff:
            q.popleft()
        unique = {s for _, s in q}
        if len(unique) < self.karma_ssid_threshold:
            return None
        key = ("karma", bssid)
        if key in self._fired:
            return None
        self._fired.add(key)
        reason = (
            f"Karma-style probe responses: BSSID {bssid} answered "
            f"{len(unique)} distinct SSIDs in {self.karma_window_s:.1f}s "
            f"(threshold {self.karma_ssid_threshold})"
        )
        return Alert(
            kind=AlertKind.KARMA,
            reason=reason,
            ts=ts,
            severity="critical",
            bssid=bssid,
            detail={
                "unique_ssids": len(unique),
                "window_s": self.karma_window_s,
                "threshold": self.karma_ssid_threshold,
            },
        )

    def _check_captive_twin(self, frame: Frame) -> Optional[Alert]:
        ssid = frame.ssid
        bssid = frame.bssid
        if not ssid or not bssid:
            return None
        sec = frame.security or "unknown"
        ads = self._ssid_ads[ssid]
        ads[bssid] = (sec, frame.channel)

        if len(ads) < 2:
            return None

        # Look for open/weaker vs stronger pair
        ranked = [(b, _rank(s), s, ch) for b, (s, ch) in ads.items()]
        ranked.sort(key=lambda x: x[1])
        weakest_b, weak_r, weak_sec, _ = ranked[0]
        strongest_b, strong_r, strong_sec, _ = ranked[-1]
        if weak_r < 0 or strong_r < 0:
            return None  # unknown security — don't guess
        if weak_r >= strong_r:
            return None  # no clear weaker twin
        if weak_r > 0 and strong_r - weak_r < 2:
            # require a meaningful gap (e.g. open vs wpa2, not wpa vs wpa2)
            if weak_sec != "open":
                return None

        key = ("captive_twin", f"{ssid}|{weakest_b}|{strongest_b}")
        if key in self._fired:
            return None
        self._fired.add(key)

        reason = (
            f"captive-portal / evil-twin candidate: SSID '{ssid}' from "
            f"BSSID {weakest_b} ({weak_sec}) alongside {strongest_b} ({strong_sec})"
        )
        return Alert(
            kind=AlertKind.CAPTIVE_TWIN,
            reason=reason,
            ts=frame.ts,
            severity="critical",
            bssid=weakest_b,
            ssid=ssid,
            detail={
                "weak_bssid": weakest_b,
                "weak_security": weak_sec,
                "strong_bssid": strongest_b,
                "strong_security": strong_sec,
            },
        )
