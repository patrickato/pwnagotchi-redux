"""Membership check for a MAC in the store."""
from __future__ import annotations

from typing import Optional

from redux.geo.db import SightingStore


def has_mac(store: SightingStore, mac: str, *, kind: str = "wifi") -> bool:
    return store.get(kind, mac) is not None


def has_mac_any_kind(store: SightingStore, mac: str) -> bool:
    mac = (mac or "").lower().strip()
    return any(s.mac == mac for s in store.query())
