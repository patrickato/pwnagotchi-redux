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

_IFACE = re.compile(r"[A-Za-z0-9_.-]{1,15}\Z")
_DRIVER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_TRANSPORTS = frozenset(("onboard", "usb"))
_MODE_RANK = {"unknown": 0, "managed": 1, "monitor": 2}


def collect(*, sys_root=Path("/sys/class/net"), run=None,
            wall_clock=time.time, monotonic=time.monotonic):
    """Return one stable sanitized sample or raise Unavailable.

    Sysfs inventory is read twice around a single bounded `iw dev` query.
    The display supports one physical onboard radio and one USB radio; duplicate
    physical devices on a transport or any unknown bus fails closed rather than
    silently hiding hardware. Multiple VIFs on one physical adapter collapse
    to the best measured current mode, preferring an actual monitor VIF.
    """
    runner = run or BoundedReader(seconds=3, command_seconds=2, clock=monotonic)
    before = inventory(sys_root)
    if not before:
        raise Unavailable("wireless_inventory_empty")
    raw = runner(["iw", "dev"])
    interfaces = iw_interfaces(raw)
    after = inventory(sys_root)
    if before != after or set(interfaces) != set(before):
        raise Unavailable("radio_identity_changed")

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
        try:
            driver = (Path(sys_root) / iface / "device" / "driver").resolve(strict=True).name
        except OSError as exc:
            raise Unavailable("radio_driver_unavailable") from exc
        if not _DRIVER.fullmatch(driver):
            raise Unavailable("radio_driver_invalid")
        raw_mode = observed.get("type")
        mode = raw_mode if raw_mode in ("monitor", "managed") else "unknown"
        key = (transport, device_path, device_inode)
        groups.setdefault(key, []).append({
            "iface": iface, "phy": phy, "driver": driver,
            "kernel_mode": mode, "arphrd": arphrd,
        })

    selected = {}
    for (transport, _device_path, _device_inode), rows in groups.items():
        if transport in selected:
            raise Unavailable("multiple_physical_radios_same_transport")
        if len({row["phy"] for row in rows}) != 1 or len({row["driver"] for row in rows}) != 1:
            raise Unavailable("physical_radio_identity_disagrees")
        chosen = sorted(rows, key=lambda row: (
            _MODE_RANK[row["kernel_mode"]], row["iface"]), reverse=True)[0]
        selected[transport] = {
            "transport": transport,
            "identity_stable": True,
            "iface": chosen["iface"],
            "driver": chosen["driver"],
            "kernel_mode": chosen["kernel_mode"],
            "arphrd": chosen["arphrd"],
        }
    sampled = wall_clock()
    try:
        clock_valid = (type(sampled) in (int, float) and math.isfinite(sampled)
                       and 0 <= sampled <= 1e12)
    except (OverflowError, ValueError, TypeError):
        clock_valid = False
    if not clock_valid:
        raise Unavailable("radio_sample_clock_invalid")
    return {"schema": 1, "sampled_utc": float(sampled),
            "radios": [selected[key] for key in ("onboard", "usb") if key in selected]}
