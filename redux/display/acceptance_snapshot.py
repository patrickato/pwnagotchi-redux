"""Private, historical visualization of sanitized Redux read-only acceptance.

This is NOT a live radio supplier. Acceptance v1 has no authenticated UTC
sample timestamp, and must never be assigned one by the previewer.
No devices, SSH, captures, or privileged processes are touched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

_FORMAT = "redux-readonly-acceptance-v1"
_DRIVER = re.compile(r"[A-Za-z0-9_-]{1,24}\Z")
_ALLOWED_RADIO = frozenset({
    "radio", "transport", "monitor_advertised", "kernel_mode", "arphrd",
    "exclusion_checks", "authenticated_owner", "decoded_reception",
    "lease_eligible", "nm_managed", "nm_state", "driver", "identity_stable",
    "usb_ancestry", "conflicting_processes",
})
_ALLOWED_TOP = frozenset({
    "format", "hardware_changes", "inventory_status", "gates", "radios",
    "usb_bus", "pan", "collector_sha256",
})


def snapshot(payload):
    """Validate one historical acceptance report; retain only safe metadata."""
    if not isinstance(payload, dict) or not set(payload).issubset(_ALLOWED_TOP):
        raise ValueError("unrecognized read-only acceptance report")
    if (payload.get("format") != _FORMAT or payload.get("hardware_changes") is not False
            or payload.get("inventory_status") != "fresh"):
        raise ValueError("acceptance report is not a fresh read-only inventory")
    gates = payload.get("gates")
    if (not isinstance(gates, dict)
            or gates.get("ethernet_return_path") != "PASS"
            or gates.get("lease_eligible") not in ("FAIL", "UNKNOWN")):
        raise ValueError("required read-only gates missing")
    radio_rows = payload.get("radios")
    if not isinstance(radio_rows, list) or not 1 <= len(radio_rows) <= 4:
        raise ValueError("invalid accepted radio count")
    seen = set()
    collected = []
    for row in radio_rows:
        if not isinstance(row, dict) or not set(row).issubset(_ALLOWED_RADIO):
            raise ValueError("unexpected radio metadata fields")
        transport = row.get("transport")
        driver = row.get("driver")
        arphrd = row.get("arphrd")
        mode = row.get("kernel_mode")
        if (transport not in ("onboard", "usb") or transport in seen
                or row.get("identity_stable") is not True
                or type(row.get("radio")) is not int or not 1 <= row["radio"] <= 4
                or not isinstance(driver, str) or not _DRIVER.fullmatch(driver)
                or type(arphrd) is not int or not 0 <= arphrd <= 65535
                or mode not in ("managed", "monitor", "unknown")
                or row.get("authenticated_owner") != "UNKNOWN"
                or row.get("decoded_reception") != "UNKNOWN"
                or row.get("lease_eligible") is not False):
            raise ValueError("unqualified or malformed radio evidence")
        seen.add(transport)
        collected.append({"transport": transport, "driver": driver,
                          "arphrd": arphrd, "mode": mode.upper()})
    # No timestamps, netdev names, addresses, MACs, SSH hosts, or owner details.
    # Never call this current or approved; the supplied report is past evidence.
    return {"schema": 1, "source": "historical_readonly_acceptance",
            "radios": collected}


def load(path):
    """Load an operator-owned existing regular JSON file, no symlink following."""
    if not isinstance(path, str) or not Path(path).is_absolute() or "\x00" in path:
        raise ValueError("absolute existing acceptance file required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 2 <= st.st_size <= 65536:
            raise ValueError("invalid acceptance file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(65537)
    finally:
        os.close(fd)
    if len(data) > 65536:
        raise ValueError("oversized acceptance report")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid acceptance JSON") from exc
    return snapshot(value)
