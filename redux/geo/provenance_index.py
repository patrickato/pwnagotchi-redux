"""Search and summarize glass-box provenance strings on sightings."""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from redux.geo.db import Sighting, SightingStore


def search_provenance(
    store: SightingStore,
    needle: str,
    *,
    kind: Optional[str] = None,
) -> List[Sighting]:
    n = (needle or "").lower().strip()
    if not n:
        return []
    rows = store.query(kind=kind) if kind else store.query()
    return [s for s in rows if n in (s.provenance or "").lower()]


def provenance_prefix_counts(store: SightingStore, *, sep: str = " ") -> Dict[str, int]:
    """Count by first token of provenance (rough source tag)."""
    c: Counter[str] = Counter()
    for s in store.query():
        prov = (s.provenance or "").strip()
        token = prov.split(sep, 1)[0] if prov else "(empty)"
        c[token] += 1
    return dict(c)
