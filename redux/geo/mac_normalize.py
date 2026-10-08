"""MAC address normalization helpers."""
from __future__ import annotations


def normalize_mac(mac: str) -> str:
    """Lowercase colon-separated form, or empty if unparseable."""
    raw = (mac or "").lower().replace(":", "").replace("-", "").replace(".", "").strip()
    if len(raw) != 12 or any(c not in "0123456789abcdef" for c in raw):
        return ""
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def is_valid_mac(mac: str) -> bool:
    return bool(normalize_mac(mac))
