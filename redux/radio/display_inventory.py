"""Read-only, fail-closed current radio inventory for the native display.

This reports kernel-observed netdev identity, driver, physical transport, mode,
and ARPHRD only. It never changes interfaces, reads RF frames, proves packet
reception, or assigns an exclusive process owner.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import time

from .local_evidence import BoundedReader, Unavailable, inventory, iw_interfaces

_IFACE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}\Z")
_DRIVER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_TRANSPORTS = frozenset(("onboard", "usb"))
_MODE_RANK = {"unknown": 0, "managed": 1, "monitor": 2}
_MAX_RADIOS = 32


def normalize_sample(sample):
    """Return an owned, bounded schema-1 copy or raise Unavailable.

    LiveRuntime treats providers as an integration boundary. Copying only the
    allowlisted scalar fields prevents later caller mutation, oversized extra
    structures, or conflicting duplicate identities from changing a published
    status snapshot.
    """
    if not isinstance(sample, dict) or sample.get("schema") != 1:
        raise Unavailable("radio_sample_invalid")
    sampled = sample.get("sampled_utc")
    try:
        clock_valid = (type(sampled) in (int, float) and math.isfinite(sampled)
                       and 0 <= sampled <= 1e12)
    except (OverflowError, TypeError, ValueError):
        clock_valid = False
    if not clock_valid:
        raise Unavailable("radio_sample_clock_invalid")
    rows = sample.get("radios")
    # Scales to the whole rig (matches the 32-radio evidence layer). Duplicate
    # *interfaces* still fail closed; multiple radios on the same transport
    # (onboard + several USB dongles) are expected, not an error.
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_RADIOS:
        raise Unavailable("radio_sample_invalid")
    ifaces = set()
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            raise Unavailable("radio_sample_invalid")
        transport = row.get("transport")
        iface = row.get("iface")
        driver = row.get("driver")
        mode = row.get("kernel_mode")
        arphrd = row.get("arphrd")
        if (transport not in _TRANSPORTS
                or not isinstance(iface, str) or not _IFACE.fullmatch(iface)
                or iface in ifaces
                or not isinstance(driver, str) or not _DRIVER.fullmatch(driver)
                or mode not in _MODE_RANK or row.get("identity_stable") is not True
                or type(arphrd) is not int or not 0 <= arphrd <= 65535):
            raise Unavailable("radio_sample_invalid")
        ifaces.add(iface)
        clean.append({"transport": transport, "identity_stable": True,
                      "iface": iface, "driver": driver,
                      "kernel_mode": mode, "arphrd": arphrd})
    # Stable order: onboard first, then externals by iface.
    clean.sort(key=lambda row: (0 if row["transport"] == "onboard" else 1, row["iface"]))
    return {"schema": 1, "sampled_utc": float(sampled), "radios": clean}


def _drivers(sys_root, identities):
    result = {}
    for iface in identities:
        try:
            driver = (Path(sys_root) / iface / "device" / "driver").resolve(strict=True).name
        except OSError as exc:
            raise Unavailable("radio_driver_unavailable") from exc
        if not _DRIVER.fullmatch(driver):
            raise Unavailable("radio_driver_invalid")
        result[iface] = driver
    return result


def collect(*, sys_root=Path("/sys/class/net"), run=None,
            wall_clock=time.time, monotonic=time.monotonic):
    """Return one stable sanitized sample or raise Unavailable.

    Sysfs inventory is read twice around a single bounded `iw dev` query.
    The display scales to the whole rig — one onboard radio plus any number of
    USB dongles — reporting one entry per physical device. An unknown bus fails
    closed rather than silently hiding hardware. Multiple VIFs on one physical
    adapter collapse to the best measured current mode, preferring a monitor VIF.
    """
    runner = run or BoundedReader(seconds=3, command_seconds=2, clock=monotonic)
    before = inventory(sys_root)
    if not before:
        raise Unavailable("wireless_inventory_empty")
    drivers_before = _drivers(sys_root, before)
    raw = runner(["iw", "dev"])
    interfaces = iw_interfaces(raw)
    after = inventory(sys_root)
    drivers_after = _drivers(sys_root, after)
    if before != after or set(interfaces) != set(before):
        raise Unavailable("radio_identity_changed")
    if drivers_before != drivers_after:
        raise Unavailable("radio_driver_changed")

    groups = {}
    for iface, identity in sorted(before.items()):
        if (not isinstance(iface, str) or not _IFACE.fullmatch(iface)
                or not isinstance(identity, tuple) or len(identity) != 9):
            raise Unavailable("radio_identity_invalid")
        _, _, ifindex, phy, arphrd, _, device_path, device_inode, transport = identity
        if transport not in _TRANSPORTS:
            raise Unavailable("radio_transport_unknown")
        observed = interfaces[iface]
        if (observed.get("phy") != phy or observed.get("ifindex") != str(ifindex)
                or type(ifindex) is not int or ifindex <= 0
                or type(arphrd) is not int or not 0 <= arphrd <= 65535
                or not re.fullmatch(r"phy[0-9]+", phy)):
            raise Unavailable("radio_kernel_identity_disagrees")
        driver = drivers_after[iface]
        raw_mode = observed.get("type")
        mode = raw_mode if raw_mode in ("monitor", "managed") else "unknown"
        key = (transport, device_path, device_inode)
        groups.setdefault(key, []).append({
            "iface": iface, "phy": phy, "driver": driver,
            "kernel_mode": mode, "arphrd": arphrd,
        })

    # One entry per *physical* device (keyed by device path+inode), so onboard +
    # any number of USB dongles are all reported; multiple VIFs on one physical
    # radio collapse to the best measured mode. No cap on same-transport count.
    selected = []
    for (transport, _device_path, _device_inode), rows in sorted(groups.items()):
        if len({row["phy"] for row in rows}) != 1 or len({row["driver"] for row in rows}) != 1:
            raise Unavailable("physical_radio_identity_disagrees")
        chosen = sorted(rows, key=lambda row: (
            _MODE_RANK[row["kernel_mode"]], row["iface"]), reverse=True)[0]
        selected.append({
            "transport": transport,
            "identity_stable": True,
            "iface": chosen["iface"],
            "driver": chosen["driver"],
            "kernel_mode": chosen["kernel_mode"],
            "arphrd": chosen["arphrd"],
        })
    sampled = wall_clock()
    try:
        clock_valid = (type(sampled) in (int, float) and math.isfinite(sampled)
                       and 0 <= sampled <= 1e12)
    except (OverflowError, ValueError, TypeError):
        clock_valid = False
    if not clock_valid:
        raise Unavailable("radio_sample_clock_invalid")
    return normalize_sample({"schema": 1, "sampled_utc": float(sampled),
            "radios": selected})
