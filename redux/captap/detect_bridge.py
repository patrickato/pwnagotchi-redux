"""Bridge: captap `Dot11Frame` → detector `Frame`.

The raw-frame tap exists because bettercap's REST stream doesn't surface management
frames — so deauth/disassoc (and raw probe-requests) reach the defensive detectors
ONLY through here. This is that connector: it maps a parsed `Dot11Frame` onto the
detector lane's public `Frame` type so captured frames flow through the same
`DetectEngine` as every other source. Read-only across the lane boundary — it
consumes the detector's input type, it doesn't modify the detector lane.
"""
from __future__ import annotations

from typing import List, Optional

from ..detect.frames import Frame, FrameType
from .dot11 import Dot11Frame

_MAP = {
    "deauth": FrameType.DEAUTH,
    "disassoc": FrameType.DISASSOC,
    "probe_req": FrameType.PROBE_REQ,
}


def to_frame(f: Dot11Frame, ts: Optional[float] = None) -> Optional[Frame]:
    """Map a parsed 802.11 management frame to a detector Frame, or None if it's a
    kind the detectors don't consume. `ts` overrides the frame's own timestamp when
    the caller tracked capture time separately."""
    ft = _MAP.get(f.kind)
    if ft is None:
        return None
    return Frame(
        type=ft,
        ts=f.ts if ts is None else ts,
        bssid=f.bssid or "",
        src=f.src or "",
        dst=f.dst or "",
        ssid=getattr(f, "ssid", "") or "",
    )


def frames_from(dot11_frames) -> List[Frame]:
    """Convert an iterable of Dot11Frames to detector Frames, dropping non-consumed kinds."""
    out: List[Frame] = []
    for f in dot11_frames:
        fr = to_frame(f)
        if fr is not None:
            out.append(fr)
    return out
