"""WPA3-downgrade / transition-mode exploitation detector.

Flags:
- Beacons advertising both SAE (WPA3) and WPA2-PSK transition mode
  (known downgrade surface)
- A BSSID that previously advertised WPA3-only later advertising WPA2-only

Advisory/passive — does not attack.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

_ADVERT = frozenset({FrameType.BEACON, FrameType.PROBE_RESP})


def _is_wpa3(sec: str) -> bool:
    s = (sec or "").lower()
    return "wpa3" in s or "sae" in s


def _is_wpa2(sec: str) -> bool:
    s = (sec or "").lower()
    return "wpa2" in s or s in ("wpa", "wpa-psk")


def _is_transition(sec: str) -> bool:
    s = (sec or "").lower()
    return (_is_wpa3(s) and _is_wpa2(s)) or "transition" in s or "wpa3-transition" in s


class WPA3DowngradeDetector:
    def __init__(self) -> None:
        self._last_sec: Dict[str, str] = {}
        self._fired: Set[str] = set()

    def reset(self) -> None:
        self._last_sec.clear()
        self._fired.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type not in _ADVERT:
            return None
        bssid = frame.bssid
        sec = frame.security
        if not bssid or not sec:
            return None

        prev = self._last_sec.get(bssid)
        self._last_sec[bssid] = sec

        if bssid in self._fired:
            return None

        if _is_transition(sec):
            self._fired.add(bssid)
            reason = (
                f"WPA3 transition mode: BSSID {bssid} SSID '{frame.ssid or '?'}' "
                f"advertises mixed WPA3/WPA2 security '{sec}' — downgrade surface"
            )
            return Alert(
                kind=AlertKind.WPA3_DOWNGRADE,
                reason=reason,
                ts=frame.ts,
                severity="warning",
                bssid=bssid,
                ssid=frame.ssid,
                detail={"security": sec, "mode": "transition"},
            )

        if prev and _is_wpa3(prev) and not _is_wpa2(prev) and _is_wpa2(sec) and not _is_wpa3(sec):
            self._fired.add(bssid)
            reason = (
                f"WPA3 downgrade observed: BSSID {bssid} SSID '{frame.ssid or '?'}' "
                f"changed from '{prev}' to '{sec}'"
            )
            return Alert(
                kind=AlertKind.WPA3_DOWNGRADE,
                reason=reason,
                ts=frame.ts,
                severity="critical",
                bssid=bssid,
                ssid=frame.ssid,
                detail={"previous": prev, "security": sec, "mode": "downgrade"},
            )

        return None

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
