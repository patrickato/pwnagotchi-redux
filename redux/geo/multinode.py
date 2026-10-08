"""Multi-node sighting merge — dedup across node IDs, keep provenance."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List

from redux.geo.db import Sighting, SightingStore


def tag_node(sighting: Sighting, node_id: str) -> Sighting:
    """Annotate provenance with source node id."""
    node_id = (node_id or "").strip()
    prov = sighting.provenance or ""
    note = f"node={node_id}"
    if note in prov:
        return sighting
    new_prov = f"{prov}; {note}" if prov else note
    return replace(sighting, provenance=new_prov, source_radio=sighting.source_radio or node_id)


def merge_from_node(
    store: SightingStore,
    sightings: Iterable[Sighting],
    node_id: str,
) -> int:
    """Insert/merge sightings from a remote node; returns rows processed."""
    n = 0
    for s in sightings:
        store.insert(tag_node(s, node_id))
        n += 1
    return n

def merge_stores(
    target: SightingStore,
    source: SightingStore,
    node_id: str,
) -> int:
    """Merge all rows from source store into target with node tagging."""
    return merge_from_node(target, source.query(), node_id)
