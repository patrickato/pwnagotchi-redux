"""Diff two sighting stores by (kind, mac) keys."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Set, Tuple

from redux.geo.db import SightingStore

Key = Tuple[str, str]


@dataclass(frozen=True)
class StoreDiff:
    only_left: tuple[Key, ...]
    only_right: tuple[Key, ...]
    both: tuple[Key, ...]
    reason: str


def _keys(store: SightingStore) -> Set[Key]:
    return {(s.kind, s.mac) for s in store.query()}


def diff_stores(left: SightingStore, right: SightingStore) -> StoreDiff:
    a, b = _keys(left), _keys(right)
    only_l = tuple(sorted(a - b))
    only_r = tuple(sorted(b - a))
    both = tuple(sorted(a & b))
    reason = (
        f"store diff: {len(only_l)} only-left, {len(only_r)} only-right, "
        f"{len(both)} shared"
    )
    return StoreDiff(only_left=only_l, only_right=only_r, both=both, reason=reason)
