"""Count observations per SSID."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional, Tuple

from redux.geo.db import SightingStore


def ssid_counts(store: SightingStore, *, kind: Optional[str] = None) -> Dict[str, int]:
    c: Counter[str] = Counter()
    rows = store.query(kind=kind) if kind else store.query()
    for s in rows:
        if s.ssid:
            c[s.ssid] += 1
    return dict(c)


def top_ssids(store: SightingStore, n: int = 10, *, kind: Optional[str] = None) -> List[Tuple[str, int]]:
    return Counter(ssid_counts(store, kind=kind)).most_common(n)
