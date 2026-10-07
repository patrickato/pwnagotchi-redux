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
  - `ble.device.new` → a BLE_ADV `Frame` (bettercap saw the device advertise). This
    feeds the BLE detectors: ble-flood (unique-address rate) and ble-tracker (known
    skimmer/tracker markers). Only fields bettercap actually provides are set —
    address always, name/company-id/service-uuid when present, never invented.
  - Deauth/disassoc floods and the surveillance-sweep pattern need *raw* deauth
    frames, which the REST event API does NOT expose. Those detectors require a
    future raw-capture tap (radiotap/pcap) — a labeled gap, NOT faked here.
  - `wifi.client.handshake` is a high-level "a handshake was captured" signal, not
    the individual EAPOL M1–M4 frames the handshake detector models, so it is NOT
    mapped to a frame (mapping it would misrepresent one event as a frame sequence).
  - Everything else (client.new, ble.lost) has no honest frame equivalent → None.

So this bridge lights up the beacon- and BLE-based detectors honestly and leaves
the raw-frame detectors dark until a capture tap exists. It never invents frames.
"""
from __future__ import annotations

from typing import List, Optional

from redux.detect.frames import Frame, FrameType


# bettercap event types (normalized by the driver) that map to a beacon sighting
_BEACON_EVENTS = {"ap.new", "ap.lost"}
# BLE device-appeared events map to a BLE advertisement sighting
_BLE_EVENTS = {"ble.new"}


def event_to_frame(event) -> Optional[Frame]:
    """Map one normalized driver Event to a detector Frame, or None if there is
    no honest frame equivalent (see module docstring for coverage)."""
    etype = getattr(event, "type", "")
    data = getattr(event, "data", {}) or {}
    ts = float(getattr(event, "at", 0.0) or 0.0)

    if etype in _BEACON_EVENTS:
        bssid = data.get("mac") or data.get("bssid") or ""
        ssid = data.get("essid") or data.get("hostname") or data.get("ssid") or ""
        return Frame(
            type=FrameType.BEACON,
            ts=ts,
            bssid=bssid,
            ssid=ssid,
            channel=data.get("channel"),
            security=str(data.get("encryption") or data.get("security") or ""),
            rssi=data.get("rssi"),
        )

    if etype in _BLE_EVENTS:
        addr = data.get("mac") or data.get("address") or data.get("bd_address") or ""
        if not addr:
            return None  # a BLE advert with no address is not a usable sighting
        name = data.get("name") or data.get("local_name") or ""
        # company_id must be an actual ID, never a vendor *name* — leave empty if absent
        cid = data.get("company_id") or data.get("vendor_id") or ""
        svc = data.get("service_uuid") or ""
        if not svc:
            services = data.get("services") or data.get("service_uuids")
            if isinstance(services, (list, tuple)) and services:
                svc = str(services[0])
        return Frame(
            type=FrameType.BLE_ADV,
            ts=ts,
            src=addr,
            ble_addr=addr,
            ble_name=name,
            ble_company_id=str(cid),
            ble_service_uuid=str(svc),
            rssi=data.get("rssi"),
        )

    return None


def events_to_frames(events) -> List[Frame]:
    """Bridge a batch of events, dropping the ones with no frame equivalent."""
    frames = []
    for e in events:
        f = event_to_frame(e)
        if f is not None:
            frames.append(f)
    return frames
