"""Group and filter sightings by source_radio."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Optional

from redux.geo.db import Sighting, SightingStore


def by_radio(store: SightingStore) -> Dict[str, List[Sighting]]:
    groups: Dict[str, List[Sighting]] = defaultdict(list)
    for s in store.query():
        key = s.source_radio or "(unknown)"
        groups[key].append(s)
    return dict(groups)


def counts_by_radio(store: SightingStore) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for s in store.query():
        c[s.source_radio or "(unknown)"] += 1
    return dict(c)


def query_radio(store: SightingStore, radio: str) -> List[Sighting]:
    radio = (radio or "").strip()
    return [s for s in store.query() if (s.source_radio or "") == radio]
