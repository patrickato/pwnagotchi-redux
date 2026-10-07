"""Bridge bettercap driver Events → detector Frames (the integration seam).

The detector pack (`redux/detect`) was built against a standalone `Frame` input so
its lane stayed unblocked. Now that the bettercap driver is in `main`, the lead
wires the two together here, as ASSIGNMENTS.md said it would.

Honest coverage — read this before trusting it:
bettercap's REST event stream is **high-level** (an AP/client/BLE device appeared,
a handshake was captured), not raw 802.11 management frames. So:
  - `wifi.ap.new` / `wifi.ap.lost` → a BEACON-class `Frame` (we saw the AP's
    beacon). This feeds the AP-watching detectors: rogue-AP/evil-twin, beacon-spam,
    Karma. Real, useful signal.
  - Deauth/disassoc floods and the surveillance-sweep pattern need *raw* deauth
    frames, which the REST event API does NOT expose. Those detectors require a
    future raw-capture tap (radiotap/pcap) — a labeled gap, NOT faked here.
  - Everything else (client.new, handshake, ble.*) has no frame equivalent → None.

So this bridge lights up the beacon-based detectors honestly and leaves the
raw-frame detectors dark until a capture tap exists. It never invents frames.
"""
from __future__ import annotations

from typing import List, Optional

from redux.detect.frames import Frame, FrameType


# bettercap event types (normalized by the driver) that map to a beacon sighting
_BEACON_EVENTS = {"ap.new", "ap.lost"}


def event_to_frame(event) -> Optional[Frame]:
    """Map one normalized driver Event to a detector Frame, or None if there is
    no honest frame equivalent (see module docstring for coverage)."""
    etype = getattr(event, "type", "")
    if etype not in _BEACON_EVENTS:
        return None
    data = getattr(event, "data", {}) or {}
    bssid = data.get("mac") or data.get("bssid") or ""
    ssid = data.get("essid") or data.get("hostname") or data.get("ssid") or ""
    return Frame(
        type=FrameType.BEACON,
        ts=float(getattr(event, "at", 0.0) or 0.0),
        bssid=bssid,
        ssid=ssid,
        channel=data.get("channel"),
        security=str(data.get("encryption") or data.get("security") or ""),
        rssi=data.get("rssi"),
    )


def events_to_frames(events) -> List[Frame]:
    """Bridge a batch of events, dropping the ones with no frame equivalent."""
    frames = []
    for e in events:
        f = event_to_frame(e)
        if f is not None:
            frames.append(f)
    return frames
