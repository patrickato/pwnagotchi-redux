"""Read-only, bounded Linux capture-interface link-type evidence.

An "iw type monitor" label or a running engine alone cannot establish
native 802.11 packet delivery. Kernel ARPHRD 801 (IEEE80211) or 803
(radiotap) is a prerequisite observation, NOT packet-reception proof.
"""
from __future__ import annotations

from pathlib import Path
import re

_SAFE_IFACE = re.compile(r"^[a-zA-Z0-9_.-]{1,15}$")
LINK_80211 = frozenset({801, 803})


def read_link_type(iface, *, sys_root=Path("/sys/class/net")):
    """Return a measured ARPHRD int, or None if unavailable or unsafe."""
    if not isinstance(iface, str) or not _SAFE_IFACE.fullmatch(iface):
        return None
    try:
        path = Path(sys_root) / iface / "type"
        if path.is_symlink():
            return None
        # Linux sysfs type entries are tiny numeric ASCII strings.
        with path.open("rb") as stream:
            raw = stream.read(16)
        if not re.fullmatch(rb"[0-9]{1,5}\n?", raw):
            return None
        value = int(raw)
        return value if 0 <= value <= 65535 else None
    except (OSError, ValueError):
        return None


def link_assessment(link_type):
    """Classify only link-layer framing; do not infer that frames arrived."""
    if type(link_type) is not int or not 0 <= link_type <= 65535:
        return "unverified"
    if link_type in LINK_80211:
        return "80211_link_type"
    if link_type == 1:
        return "ethernet_link_type"
    return "other_link_type"
