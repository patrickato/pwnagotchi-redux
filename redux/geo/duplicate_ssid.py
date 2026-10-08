"""Find SSIDs advertised by multiple distinct BSSIDs."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set

from redux.geo.db import SightingStore


def ssid_to_macs(store: SightingStore) -> Dict[str, Set[str]]:
    m: Dict[str, Set[str]] = defaultdict(set)
    for s in store.query(kind="wifi"):
        if s.ssid:
            m[s.ssid].add(s.mac)
    return {k: v for k, v in m.items()}


def multi_bssid_ssids(store: SightingStore, *, min_macs: int = 2) -> List[dict]:
    out = []
    for ssid, macs in ssid_to_macs(store).items():
        if len(macs) >= min_macs:
            out.append({
                "ssid": ssid,
                "mac_count": len(macs),
                "macs": sorted(macs),
                "reason": f"SSID '{ssid}' seen on {len(macs)} BSSID(s)",
            })
    out.sort(key=lambda x: -x["mac_count"])
    return out
