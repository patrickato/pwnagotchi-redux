"""Hardware auto-profiler — plug something in, learn what it unlocks.

Your biggest fear is owning a capability you didn't know you had. This closes that
for hardware: it enumerates every plugged USB device, maps the ones it recognizes
to the capability they unlock (and the manifest feature + its readiness), and —
crucially — flags the ones it does NOT recognize, so an unknown dongle is surfaced
rather than silently ignored. The answer to "what can I do with what's plugged in
right now?" in one place.

Pure/testable: `profile_usb` works over a plain device list; `scan_usb` reads
/sys/bus/usb/devices (same source peripherals.py uses) and is injected for tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# known USB (idVendor, idProduct) -> (friendly name, manifest feature it unlocks).
# Lowercase hex, no colons. Covers the gear this project cares about + common kit.
_KNOWN: Dict[Tuple[str, str], Tuple[str, str]] = {
    # RTL-SDR (RTL2832U) -> SDR receive/decode
    ("0bda", "2838"): ("RTL-SDR (RTL2832U)", "sdr.rx"),
    ("0bda", "2832"): ("RTL-SDR (RTL2832U)", "sdr.rx"),
    ("0bda", "2831"): ("RTL-SDR (RTL2832U)", "sdr.rx"),
    ("0bda", "2837"): ("RTL-SDR (RTL2832U)", "sdr.rx"),
    ("1d19", "1101"): ("RTL-SDR (Dexatek)", "sdr.rx"),
    # HackRF -> sub-GHz transmit/replay (power-tier)
    ("1d50", "6089"): ("HackRF One", "subghz.replay"),
    ("1d50", "cc15"): ("rad1o (HackRF)", "subghz.replay"),
    # monitor/injection Wi-Fi adapters -> Wi-Fi capture
    ("0e8d", "7612"): ("MediaTek MT7612U (Alfa AWUS036ACM)", "wifi.capture"),
    ("0e8d", "7610"): ("MediaTek MT7610U", "wifi.capture"),
    ("0bda", "8812"): ("Realtek RTL8812AU (Alfa AWUS036ACH)", "wifi.capture"),
    ("0bda", "8813"): ("Realtek RTL8813AU", "wifi.capture"),
    ("148f", "5370"): ("Ralink RT5370", "wifi.capture"),
    ("148f", "3070"): ("Ralink RT3070", "wifi.capture"),
    ("0cf3", "9271"): ("Atheros AR9271 (Alfa AWUS036NHA)", "wifi.capture"),
    # Bluetooth dongles -> BLE scan
    ("0a12", "0001"): ("CSR Bluetooth dongle", "ble.scan"),
    ("8087", "0a2a"): ("Intel Bluetooth", "ble.scan"),
    ("0a5c", "21e8"): ("Broadcom Bluetooth", "ble.scan"),
    # GPS (common USB-serial GPS bridges)
    ("1546", "01a7"): ("u-blox GPS", "gps.position"),
    ("067b", "2303"): ("Prolific USB-serial (often GPS)", "gps.position"),
    ("10c4", "ea60"): ("Silicon Labs CP210x (serial/GPS)", "gps.position"),
}


@dataclass(frozen=True)
class UsbDevice:
    vid: str
    pid: str
    name: str = ""

    @property
    def id(self) -> str:
        return f"{self.vid}:{self.pid}"


@dataclass(frozen=True)
class ProfileEntry:
    device: str            # friendly / reported name
    usb_id: str            # vid:pid
    known: bool
    feature: str = ""      # manifest feature id it unlocks ("" if unknown)
    capability: str = ""   # the Cap it maps to, if any
    state: str = ""        # the feature's live readiness, if resolvable
    reason: str = ""

    def to_dict(self) -> dict:
        return {"device": self.device, "usb_id": self.usb_id, "known": self.known,
                "feature": self.feature, "capability": self.capability,
                "state": self.state, "reason": self.reason}


def profile_usb(devices, *, manifest_lookup=None, readiness=None) -> List[ProfileEntry]:
    """Map plugged USB devices to what they unlock. `manifest_lookup(feature_id)`
    and `readiness(feature)` are injected so this stays pure; when omitted it still
    reports known/unknown and the feature id."""
    out: List[ProfileEntry] = []
    for d in devices:
        key = (d.vid.lower(), d.pid.lower())
        if key in _KNOWN:
            name, feature = _KNOWN[key]
            cap = state = ""
            if manifest_lookup is not None:
                f = manifest_lookup(feature)
                if f is not None:
                    cap = getattr(f, "cap", "") or ""
                    if readiness is not None:
                        st, _ = readiness(f)
                        state = getattr(st, "value", str(st))
            out.append(ProfileEntry(
                device=name, usb_id=d.id, known=True, feature=feature,
                capability=cap, state=state,
                reason=f"{name} → unlocks {feature}" + (f" [{state}]" if state else "")))
        else:
            out.append(ProfileEntry(
                device=d.name or d.id, usb_id=d.id, known=False,
                reason="plugged in but not recognized — check what it is; it may add a capability"))
    return out


def scan_usb(*, usb_root=Path("/sys/bus/usb/devices")) -> List[UsbDevice]:
    """Read plugged USB devices from sysfs (vid, pid, product name). Fail-safe to
    an empty list. Same source peripherals.py reads."""
    devices: List[UsbDevice] = []
    try:
        entries = sorted(usb_root.iterdir())
    except OSError:
        return devices
    for dev in entries:
        try:
            vid = (dev / "idVendor").read_text().strip().lower()
            pid = (dev / "idProduct").read_text().strip().lower()
        except OSError:
            continue
        name = ""
        try:
            name = (dev / "product").read_text().strip()
        except OSError:
            pass
        devices.append(UsbDevice(vid=vid, pid=pid, name=name))
    return devices


def profile(*, usb_root=Path("/sys/bus/usb/devices")) -> List[ProfileEntry]:
    """Scan + profile against the live capability manifest (device-side default)."""
    from . import manifest as M
    return profile_usb(scan_usb(usb_root=usb_root),
                       manifest_lookup=M.get, readiness=M.readiness)
