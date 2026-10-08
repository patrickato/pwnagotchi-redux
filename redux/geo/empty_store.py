"""Helpers for empty / non-empty store checks."""
from __future__ import annotations

from redux.geo.db import SightingStore


def is_empty(store: SightingStore) -> bool:
    return store.count() == 0


def is_nonempty(store: SightingStore) -> bool:
    return store.count() > 0
