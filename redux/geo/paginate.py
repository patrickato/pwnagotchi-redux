"""Simple offset/limit pagination for sighting lists."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Page:
    items: tuple
    offset: int
    limit: int
    total: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


def paginate(items: Sequence[T], *, offset: int = 0, limit: int = 50) -> Page:
    offset = max(0, int(offset))
    limit = max(1, int(limit))
    total = len(items)
    slice_ = list(items[offset : offset + limit])
    return Page(items=tuple(slice_), offset=offset, limit=limit, total=total)
