"""Conservative radio telemetry for Redux's native 480x320 diagnostics.

No sysfs, netdev, RF, or network access occurs here. This pure model only
interprets the same bounded API snapshot as the framebuffer dashboard.
Kernel link type is NOT evidence of decoded received 802.11 frames.
"""
from __future__ import annotations

from math import isfinite
import re
import time

_IFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}\Z")
_DRIVER = re.compile(r"[A-Za-z0-9_-]{1,24}\Z")


def _clock(value):
    if type(value) not in (int, float):
        return None
    try:
        if isfinite(value) and 0 <= value <= 1e12:
            return float(value)
    except (OverflowError, ValueError, TypeError):
        pass
    return None


def _link(value):
    return value if type(value) is int and 0 <= value <= 65535 else None


def _safe_label(value, pattern):
    return value if isinstance(value, str) and pattern.fullmatch(value) else "UNKNOWN"


def _historical(payload):
    """Render offline evidence as SAVED, never as CURRENT or receive-qualified."""
    if (not isinstance(payload, dict) or payload.get("schema") != 1
            or payload.get("source") != "historical_readonly_acceptance"):
        return None
    rows = payload.get("radios")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4:
        return None
    selected = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        transport = row.get("transport")
        driver = row.get("driver")
        arphrd = _link(row.get("arphrd"))
        mode = row.get("mode")
        if (transport not in ("onboard", "usb") or transport in selected
                or not isinstance(driver, str) or not _DRIVER.fullmatch(driver)
                or arphrd is None or mode not in ("MONITOR", "MANAGED", "UNKNOWN")):
            return None
        selected[transport] = {"state": "SAVED", "iface": "UNKNOWN",
                               "driver": driver, "mode": mode, "link": arphrd,
                               "reception": "UNVERIFIED"}
    return selected


def model(report, *, now=None):
    """Display only current measured metadata; leave reception unverified.

    Optional radio_inventory is a future, separately provisioned, independently
    sampled status field. It must have a fresh explicit sampling clock and
    stable identities to be displayed. No caller-supplied flag can qualify RF.
    """
    now = time.time() if now is None else now
    current = _clock(now)
    root = report if isinstance(report, dict) else {}
    runtime = root.get("runtime", {})
    runtime = runtime if isinstance(runtime, dict) else {}
    at = _clock(runtime.get("updated_utc"))
    fresh = (current is not None and at is not None and
             -30 <= current - at <= 20)

    capture = {"iface": "UNKNOWN", "state": "NOT CURRENT",
               "link": None, "framing": "UNVERIFIED",
               "reception": "UNVERIFIED"}
    if fresh:
        capture["iface"] = _safe_label(runtime.get("iface"), _IFACE)
        state = runtime.get("state")
        capture["state"] = (state.upper() if state in (
            "running", "starting", "starting_engine", "rotating",
            "stopped", "degraded", "storage_paused") else "UNKNOWN")
        capture["link"] = _link(runtime.get("capture_link_type"))
        if capture["link"] in (801, 803):
            capture["framing"] = "802.11 HEADER TYPE"
        elif capture["link"] == 1:
            capture["framing"] = "NON-NATIVE LINK"
        elif capture["link"] is not None:
            capture["framing"] = "OTHER LINK TYPE"

    inventory = root.get("radio_inventory")
    usb = {"state": "NOT REPORTED", "iface": "UNKNOWN", "driver": "UNKNOWN",
           "mode": "UNKNOWN", "link": None, "reception": "UNVERIFIED"}
    onboard = {"state": "NOT REPORTED", "iface": "UNKNOWN", "driver": "UNKNOWN",
               "mode": "UNKNOWN", "link": None, "reception": "UNVERIFIED"}
    if not isinstance(inventory, dict) or inventory.get("schema") != 1:
        history = _historical(root.get("radio_acceptance_snapshot"))
        if history is not None:
            return {"capture": capture, "onboard": history.get("onboard", onboard),
                    "usb": history.get("usb", usb), "inventory": "SAVED SNAPSHOT"}
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "NOT REPORTED"}
    sampled = _clock(inventory.get("sampled_utc"))
    if current is None or sampled is None or not -30 <= current - sampled <= 20:
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "STALE"}
    radios = inventory.get("radios")
    if not isinstance(radios, list) or not 1 <= len(radios) <= 4:
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "INVALID"}
    seen = set()
    selected = {}
    for entry in radios:
        if not isinstance(entry, dict):
            return {"capture": capture, "onboard": onboard, "usb": usb,
                    "inventory": "INVALID"}
        transport = entry.get("transport")
        if (transport not in ("onboard", "usb") or transport in seen
                or entry.get("identity_stable") is not True):
            return {"capture": capture, "onboard": onboard, "usb": usb,
                    "inventory": "INVALID"}
        seen.add(transport)
        mode = entry.get("kernel_mode")
        driver = entry.get("driver")
        iface = entry.get("iface", "UNKNOWN")
        arphrd = _link(entry.get("arphrd"))
        if (mode not in ("monitor", "managed", "unknown")
                or not isinstance(driver, str) or not _DRIVER.fullmatch(driver)
                or not isinstance(iface, str) or not _IFACE.fullmatch(iface)
                or arphrd is None):
            return {"capture": capture, "onboard": onboard, "usb": usb,
                    "inventory": "INVALID"}
        selected[transport] = {"state": "OBSERVED", "iface": iface,
                               "driver": driver, "mode": mode.upper(),
                               "link": arphrd, "reception": "UNVERIFIED"}
    return {"capture": capture, "onboard": selected.get("onboard", onboard),
            "usb": selected.get("usb", usb), "inventory": "CURRENT"}
