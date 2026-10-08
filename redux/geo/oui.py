"""MAC OUI prefix helpers with a small built-in vendor table.

Not a full IEEE DB — enough for glass-box labels in survey UIs.
"""
from __future__ import annotations

from typing import Dict, Optional

# Truncated local map (prefix without separators, lowercase)
_OUI: Dict[str, str] = {
    "001122": "ExampleCorp",
    "aabbcc": "DemoVendor",
    "f0d1a9": "Apple",
    "3c22fb": "Apple",
    "dc56e7": "Samsung",
    "b827eb": "RaspberryPi",
    "dca632": "RaspberryPi",
    "00e04c": "Realtek",
}


def normalize_mac(mac: str) -> str:
    return (mac or "").lower().replace(":", "").replace("-", "").replace(".", "")


def oui_prefix(mac: str) -> str:
    n = normalize_mac(mac)
    return n[:6] if len(n) >= 6 else n


def lookup_oui(mac: str, table: Optional[Dict[str, str]] = None) -> Optional[str]:
    t = table if table is not None else _OUI
    return t.get(oui_prefix(mac))


def label_mac(mac: str, table: Optional[Dict[str, str]] = None) -> str:
    vendor = lookup_oui(mac, table)
    if vendor:
        return f"{mac} ({vendor})"
    return mac or ""
