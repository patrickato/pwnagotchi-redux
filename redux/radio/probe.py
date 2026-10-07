"""Capability probe — turn the live radio hardware into `Radio` records.

The parsing is pure (feed it `iw phy` / `iw dev` text) so it unit-tests with no
hardware. `probe()` is the thin live wrapper that runs the commands + reads sysfs
and calls `build_radios`.

Honest limit: `iw` does not report whether injection actually works. We mark
`inject` from a known-good-driver allowlist as a *heuristic*; a real per-adapter
injection self-test is a separate task (see TASKS.md) and should override this.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Optional

from .orchestrator import Radio

# Drivers known to support injection in monitor mode (heuristic; self-test overrides).
_INJECT_DRIVERS = {
    "mt76x2u", "mt76x0u", "mt7921u", "mt7612u", "rtl88xxau", "88XXau",
    "rt2800usb", "ath9k_htc", "ath9k", "carl9170", "rtl8187", "rtl8812au",
}
# High-draw adapters (11ac/USB3) prone to brownout on a shared/USB-2 bus.
_HIGH_DRAW = {"mt76x2u", "mt7612u", "rtl88xxau", "88XXau", "rtl8812au"}
# Onboard Pi Wi-Fi.
_ONBOARD_DRIVERS = {"brcmfmac"}


def _freq_to_band(mhz: int) -> Optional[str]:
    if 2400 <= mhz <= 2500:
        return "2.4"
    if 5000 <= mhz <= 5895:
        return "5"
    if 5925 <= mhz <= 7125:
        return "6"
    return None


def parse_iw_dev(text: str) -> dict:
    """phy -> first netdev name, from `iw dev` output."""
    phy_iface: dict = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*phy#(\d+)", line)
        if m:
            cur = "phy" + m.group(1)
            continue
        m = re.search(r"\bInterface\s+(\S+)", line)
        if m and cur:
            phy_iface.setdefault(cur, m.group(1))
    return phy_iface


def parse_iw_phy(text: str) -> dict:
    """phy -> {monitor: bool, bands: set}, from `iw phy` output."""
    phys: dict = {}
    cur = None
    for line in text.splitlines():
        m = re.match(r"\s*Wiphy\s+(\S+)", line)
        if m:
            cur = m.group(1)
            phys[cur] = {"monitor": False, "bands": set()}
            continue
        if cur is None:
            continue
        if re.search(r"\*\s*monitor\b", line):
            phys[cur]["monitor"] = True
        m = re.search(r"\*\s*(\d{3,5})(?:\.\d+)?\s*MHz", line)
        if m:
            band = _freq_to_band(int(m.group(1)))
            if band:
                phys[cur]["bands"].add(band)
    return phys


def build_radios(iw_phy_text: str, iw_dev_text: str, phy_meta: Optional[dict] = None) -> list:
    """Combine parsed `iw` output + per-phy metadata (driver/usb_gen) into Radios."""
    phy_meta = phy_meta or {}
    phys = parse_iw_phy(iw_phy_text)
    dev = parse_iw_dev(iw_dev_text)
    radios = []
    for phy, info in phys.items():
        meta = phy_meta.get(phy, {})
        driver = meta.get("driver", "")
        radios.append(Radio(
            iface=dev.get(phy, phy),
            phy=phy,
            bands=frozenset(info["bands"] or {"2.4"}),
            monitor=info["monitor"],
            inject=bool(info["monitor"] and driver in _INJECT_DRIVERS),
            driver=driver,
            onboard=driver in _ONBOARD_DRIVERS,
            usb_gen=meta.get("usb_gen"),
            high_draw=driver in _HIGH_DRAW,
        ))
    return radios


# ---- live layer (hardware; not exercised by the unit tests) ----
def _run(cmd: list) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def _driver_of(iface: str) -> str:
    try:
        link = os.readlink(f"/sys/class/net/{iface}/device/driver")
        return os.path.basename(link)
    except OSError:
        return ""


def _usb_gen_of(iface: str) -> Optional[int]:
    # Walk up the device chain looking for a USB 'speed' (Mbps): 480=USB2, 5000+=USB3.
    base = f"/sys/class/net/{iface}/device"
    for _ in range(6):
        speed_path = os.path.join(base, "speed")
        try:
            with open(speed_path) as f:
                mbps = float(f.read().strip())
                return 3 if mbps >= 5000 else 2
        except (OSError, ValueError):
            pass
        base = os.path.dirname(base)
        if base in ("", "/", "/sys"):
            break
    return None


def probe() -> list:
    """Live probe on a real device."""
    iw_phy = _run(["iw", "phy"])
    iw_dev = _run(["iw", "dev"])
    dev = parse_iw_dev(iw_dev)
    phy_meta = {}
    for phy, iface in dev.items():
        phy_meta[phy] = {"driver": _driver_of(iface), "usb_gen": _usb_gen_of(iface)}
    return build_radios(iw_phy, iw_dev, phy_meta)
