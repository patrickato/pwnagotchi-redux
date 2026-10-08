"""MAC address normalization helpers."""
from __future__ import annotations

import re

_HEX = re.compile(r"[^0-9a-fA-F]")


def normalize_mac(mac: str) -> str:
    """Lowercase colon-separated MAC (aa:bb:cc:dd:ee:ff)."""
    hexdigits = _HEX.sub("", mac or "").lower()
    if len(hexdigits) != 12:
        return (mac or "").lower().strip()
    return ":".join(hexdigits[i : i + 2] for i in range(0, 12, 2))


def is_valid_mac(mac: str) -> bool:
    hexdigits = _HEX.sub("", mac or "")
    return len(hexdigits) == 12 and all(c in "0123456789abcdefABCDEF" for c in hexdigits)


def is_locally_administered(mac: str) -> bool:
    n = _HEX.sub("", mac or "")
    if len(n) < 2:
        return False
    return (int(n[0:2], 16) & 0x02) != 0
