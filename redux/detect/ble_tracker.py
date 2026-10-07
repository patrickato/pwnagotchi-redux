"""BLE tracker / AirTag / skimmer detector (passive).

Flags:
- Apple company id 0x004C Find My / AirTag-style advertisements
- Local names matching common HC-05/06 / "BT-POS" skimmer modules

Raises glass-box alerts only — never connects or transmits.
"""
from __future__ import annotations

from typing import List, Optional, Set

from redux.detect.alerts import Alert, AlertKind
from redux.detect.frames import Frame, FrameType

# Apple Inc. Bluetooth company identifier
_APPLE_CID = "0x004c"

# Substrings often used by cheap serial BLE modules in skimmer builds
_SKIMMER_NAME_MARKERS = (
    "hc-05",
    "hc-06",
    "hc05",
    "hc06",
    "bt-pos",
    "pos-bt",
    "skimmer",
    "m35",
    "m30",
)


def _norm_cid(cid: str) -> str:
    c = (cid or "").lower().strip()
    if c.startswith("0x"):
        return c
    # allow bare hex "4c" / "004c"
    if c and all(ch in "0123456789abcdef" for ch in c):
        return "0x" + c.zfill(4)
    return c


class BLETrackerDetector:
    """Passive BLE advertisement classifier for trackers and skimmers."""

    def __init__(self) -> None:
        self._seen_tracker: Set[str] = set()
        self._seen_skimmer: Set[str] = set()

    def reset(self) -> None:
        self._seen_tracker.clear()
        self._seen_skimmer.clear()

    def feed(self, frame: Frame) -> Optional[Alert]:
        if frame.type is not FrameType.BLE_ADV:
            return None
        addr = frame.ble_addr or frame.src or frame.bssid
        if not addr:
            return None

        name = (frame.ble_name or "").lower()
        cid = _norm_cid(frame.ble_company_id)

        # Skimmer name match takes priority (more specific defense demo)
        if any(m in name for m in _SKIMMER_NAME_MARKERS):
            if addr in self._seen_skimmer:
                return None
            self._seen_skimmer.add(addr)
            reason = (
                f"BLE skimmer-module candidate: addr {addr} local-name "
                f"'{frame.ble_name}' matches known HC-0x/POS marker"
            )
            return Alert(
                kind=AlertKind.BLE_SKIMMER,
                reason=reason,
                ts=frame.ts,
                severity="critical",
                bssid=addr,
                detail={"ble_name": frame.ble_name, "company_id": cid},
            )

        # Apple Find My / AirTag family: company 0x004C
        if cid == _APPLE_CID:
            if addr in self._seen_tracker:
                return None
            self._seen_tracker.add(addr)
            label = frame.ble_name or "(no local name)"
            reason = (
                f"BLE tracker / Find My candidate: addr {addr} "
                f"Apple company id {cid}, name '{label}'"
            )
            return Alert(
                kind=AlertKind.BLE_TRACKER,
                reason=reason,
                ts=frame.ts,
                severity="warning",
                bssid=addr,
                detail={
                    "ble_name": frame.ble_name,
                    "company_id": cid,
                    "service_uuid": frame.ble_service_uuid,
                },
            )

        return None

    def feed_many(self, frames: List[Frame]) -> List[Alert]:
        out: List[Alert] = []
        for f in frames:
            a = self.feed(f)
            if a is not None:
                out.append(a)
        return out
