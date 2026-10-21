"""Conservative radio telemetry for Redux's native 480x320 diagnostics.

No sysfs, netdev, RF, or network access occurs here. This pure model only
interprets the same bounded API snapshot as the framebuffer dashboard.
Kernel link type is NOT evidence of decoded received 802.11 frames.
"""
from __future__ import annotations

from math import isfinite
import re
import time

_IFACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}\Z")
_DRIVER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


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


def _base_readiness(state="UNVERIFIED", *blockers, runtime_age=None,
                    inventory_age=None):
    return {"state": state, "blockers": list(blockers),
            "runtime_age_seconds": runtime_age,
            "inventory_age_seconds": inventory_age}


def model(report, *, now=None):
    """Display only current measured metadata; leave reception unverified.

    Optional radio_inventory is supplied by the live runtime's bounded,
    read-only display collector. It must have a fresh sample clock and stable
    identities to be displayed. No inventory field or flag can qualify RF.
    """
    now = time.time() if now is None else now
    current = _clock(now)
    root = report if isinstance(report, dict) else {}
    runtime = root.get("runtime", {})
    runtime = runtime if isinstance(runtime, dict) else {}
    at = _clock(runtime.get("updated_utc"))
    fresh = (current is not None and at is not None and
             -30 <= current - at <= 20)
    runtime_age = round(max(0.0, current - at), 1) if fresh else None

    capture = {"iface": "UNKNOWN", "state": "NOT CURRENT",
               "link": None, "framing": "UNVERIFIED",
               "reception": "UNVERIFIED"}
    if fresh:
        capture["iface"] = _safe_label(runtime.get("iface"), _IFACE)
        state = runtime.get("state")
        capture["state"] = (state.upper() if state in (
            "running", "starting", "starting_engine", "rotating",
            "stopped", "degraded", "storage_paused", "lease_blocked",
            "termination_pending", "telemetry_degraded") else "UNKNOWN")
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
                    "usb": history.get("usb", usb), "inventory": "SAVED SNAPSHOT",
                    "readiness": _base_readiness("HISTORICAL", "NOT LIVE")}
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "NOT REPORTED",
                "readiness": _base_readiness("BLOCKED", "INVENTORY MISSING",
                                              runtime_age=runtime_age)}
    sampled = _clock(inventory.get("sampled_utc"))
    if current is None or sampled is None or not -30 <= current - sampled <= 20:
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "STALE",
                "readiness": _base_readiness("BLOCKED", "INVENTORY STALE",
                                              runtime_age=runtime_age)}
    inventory_age = round(max(0.0, current - sampled), 1)
    # Both clocks can be individually fresh yet describe different hardware
    # generations. The collector normally runs every ten seconds, so a larger
    # separation is conflicting evidence rather than one coherent view.
    if at is None or abs(at - sampled) > 15:
        return {"capture": capture, "onboard": onboard, "usb": usb,
                "inventory": "CONFLICT",
                "readiness": _base_readiness("BLOCKED", "SOURCE CLOCK SKEW",
                                              runtime_age=runtime_age,
                                              inventory_age=inventory_age)}
    radios = inventory.get("radios")
    if not isinstance(radios, list) or not 1 <= len(radios) <= 32:
        return {"capture": capture, "onboard": onboard, "usb": usb, "externals": [],
                "inventory": "INVALID",
                "readiness": _base_readiness("BLOCKED", "INVENTORY INVALID",
                                              runtime_age=runtime_age,
                                              inventory_age=inventory_age)}
    seen_ifaces = set()
    onboard_row = None
    externals = []            # every non-onboard radio, in inventory order
    for entry in radios:
        if not isinstance(entry, dict):
            return {"capture": capture, "onboard": onboard, "usb": usb, "externals": [],
                    "inventory": "INVALID",
                    "readiness": _base_readiness("BLOCKED", "INVENTORY INVALID",
                                                  runtime_age=runtime_age,
                                                  inventory_age=inventory_age)}
        transport = entry.get("transport")
        if (transport not in ("onboard", "usb")
                or (transport == "onboard" and onboard_row is not None)
                or entry.get("identity_stable") is not True):
            return {"capture": capture, "onboard": onboard, "usb": usb, "externals": [],
                    "inventory": "INVALID",
                    "readiness": _base_readiness("BLOCKED", "IDENTITY UNSTABLE",
                                                  runtime_age=runtime_age,
                                                  inventory_age=inventory_age)}
        mode = entry.get("kernel_mode")
        driver = entry.get("driver")
        iface = entry.get("iface", "UNKNOWN")
        arphrd = _link(entry.get("arphrd"))
        if (mode not in ("monitor", "managed", "unknown")
                or not isinstance(driver, str) or not _DRIVER.fullmatch(driver)
                or not isinstance(iface, str) or not _IFACE.fullmatch(iface)
                or iface in seen_ifaces or arphrd is None):
            return {"capture": capture, "onboard": onboard, "usb": usb, "externals": [],
                    "inventory": "INVALID",
                    "readiness": _base_readiness("BLOCKED", "INVENTORY INVALID",
                                                  runtime_age=runtime_age,
                                                  inventory_age=inventory_age)}
        seen_ifaces.add(iface)
        row = {"state": "OBSERVED", "iface": iface, "transport": transport,
               "driver": driver, "mode": mode.upper(),
               "link": arphrd, "reception": "UNVERIFIED"}
        if transport == "onboard":
            onboard_row = row
        else:
            externals.append(row)
    blockers = []
    if not fresh:
        blockers.append("RUNTIME STALE")
    all_rows = ([onboard_row] if onboard_row else []) + externals
    matching = [r for r in all_rows if r["iface"] == capture["iface"]]
    if fresh and len(matching) != 1:
        blockers.append("CAPTURE IDENTITY MISMATCH")
    if capture["link"] not in (801, 803):
        blockers.append("CAPTURE FRAMING")
    if onboard_row is None:
        blockers.append("ONBOARD MISSING")
    if not externals:
        # Onboard-only is a complete device now, so this is informational, not a
        # hard block on its own.
        blockers.append("NO EXTERNAL RADIO")
    # Display inventory has no NM policy, socket exclusivity, decoded-frame,
    # or physical qualification proof. Keep these explicit even if framing is
    # compatible and the runtime-selected interface matches one inventory row.
    blockers.extend(("POLICY UNMEASURED", "RX UNVERIFIED", "PHYSICAL UNVERIFIED"))
    state = "BLOCKED" if any(item in blockers for item in (
        "RUNTIME STALE", "CAPTURE IDENTITY MISMATCH", "CAPTURE FRAMING",
        "ONBOARD MISSING")) else "UNVERIFIED"
    return {"capture": capture,
            "onboard": onboard_row or onboard,
            "usb": externals[0] if externals else usb,   # primary external (back-compat)
            "externals": externals,                        # the full list, nothing hidden
            "inventory": "CURRENT",
            "readiness": _base_readiness(state, *blockers,
                                          runtime_age=runtime_age,
                                          inventory_age=inventory_age)}
