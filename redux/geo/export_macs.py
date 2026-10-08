"""Export unique MACs from the store as plain text."""
from __future__ import annotations

from typing import List, Optional

from redux.geo.db import SightingStore


def list_macs(store: SightingStore, *, kind: Optional[str] = None) -> List[str]:
    rows = store.query(kind=kind) if kind else store.query()
    return sorted({s.mac for s in rows if s.mac})


def export_macs_text(store: SightingStore, *, kind: Optional[str] = None) -> str:
    return "\n".join(list_macs(store, kind=kind)) + ("\n" if store.count(kind=kind) else "")
