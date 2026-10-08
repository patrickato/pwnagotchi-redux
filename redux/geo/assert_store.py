"""Assertion helpers for geo store unit tests."""
from __future__ import annotations

from redux.geo.db import SightingStore


def assert_count(store: SightingStore, expected: int, *, kind: str | None = None) -> None:
    actual = store.count(kind=kind) if kind else store.count()
    if actual != expected:
        raise AssertionError(f"expected count={expected}, got {actual}")


def assert_has(store: SightingStore, kind: str, mac: str) -> None:
    if store.get(kind, mac) is None:
        raise AssertionError(f"expected {kind}/{mac} present")


def assert_missing(store: SightingStore, kind: str, mac: str) -> None:
    if store.get(kind, mac) is not None:
        raise AssertionError(f"expected {kind}/{mac} absent")
