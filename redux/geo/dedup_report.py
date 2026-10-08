"""Batch dedup report — summarize collisions before inserting into the store."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from redux.geo.db import Sighting


@dataclass(frozen=True)
class DedupReport:
    unique: int
    duplicates: int
    groups: int
    reason: str


def dedup_report(sightings: Sequence[Sighting]) -> DedupReport:
    groups: Dict[Tuple[str, str], List[Sighting]] = defaultdict(list)
    for s in sightings:
        groups[(s.kind, s.mac)].append(s)
    unique = len(groups)
    duplicates = sum(len(v) - 1 for v in groups.values() if len(v) > 1)
    multi = sum(1 for v in groups.values() if len(v) > 1)
    reason = (
        f"dedup report: {len(sightings)} rows → {unique} unique keys, "
        f"{duplicates} duplicate(s) across {multi} key(s)"
    )
    return DedupReport(unique=unique, duplicates=duplicates, groups=multi, reason=reason)
