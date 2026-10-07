"""Survey coverage / gap grid.

Buckets observer positions into a fixed lat/lon cell grid so a driver can
see which cells have been visited vs still empty inside a bounding box.
Uses simple degree cells (not geohash) for transparent math and easy tests.

Pure logic — no GPS hardware, no redux.engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Set, Tuple

CellKey = Tuple[int, int]  # (lat_i, lon_i)


class _Point(Protocol):
    lat: Optional[float]
    lon: Optional[float]


@dataclass(frozen=True)
class GridCell:
    lat_i: int
    lon_i: int
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    hit_count: int = 0

    @property
    def covered(self) -> bool:
        return self.hit_count > 0

    @property
    def center(self) -> Tuple[float, float]:
        return ((self.lat_min + self.lat_max) / 2.0, (self.lon_min + self.lon_max) / 2.0)


@dataclass
class CoverageGrid:
    """Mutable coverage map over a lat/lon bounding box."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    cell_deg: float = 0.001  # ~111 m at equator
    _hits: Dict[CellKey, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cell_deg <= 0:
            raise ValueError("cell_deg must be positive")
        if self.lat_max < self.lat_min or self.lon_max < self.lon_min:
            raise ValueError("invalid bounding box")

    def _index(self, lat: float, lon: float) -> Optional[CellKey]:
        if not (self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max):
            return None
        # Floor relative to origin so edges stay stable
        lat_i = math.floor((lat - self.lat_min) / self.cell_deg)
        lon_i = math.floor((lon - self.lon_min) / self.cell_deg)
        # A point exactly on the max edge floors to one cell past the grid that
        # all_cells() enumerates; clamp it into the last cell so covered counts
        # can never exceed total (no ratio>1 / negative-gap bookkeeping).
        n_lat = max(1, math.ceil((self.lat_max - self.lat_min) / self.cell_deg))
        n_lon = max(1, math.ceil((self.lon_max - self.lon_min) / self.cell_deg))
        lat_i = min(max(lat_i, 0), n_lat - 1)
        lon_i = min(max(lon_i, 0), n_lon - 1)
        return (lat_i, lon_i)

    def _bounds(self, key: CellKey) -> Tuple[float, float, float, float]:
        lat_i, lon_i = key
        la0 = self.lat_min + lat_i * self.cell_deg
        lo0 = self.lon_min + lon_i * self.cell_deg
        return (la0, la0 + self.cell_deg, lo0, lo0 + self.cell_deg)

    def mark(self, lat: float, lon: float) -> bool:
        """Record a visit. Returns False if outside the bounding box."""
        key = self._index(lat, lon)
        if key is None:
            return False
        self._hits[key] = self._hits.get(key, 0) + 1
        return True

    def mark_many(self, points: Iterable[Tuple[float, float]]) -> int:
        n = 0
        for lat, lon in points:
            if self.mark(lat, lon):
                n += 1
        return n

    def mark_samples(self, samples: Iterable[_Point]) -> int:
        n = 0
        for s in samples:
            if s.lat is None or s.lon is None:
                continue
            if self.mark(float(s.lat), float(s.lon)):
                n += 1
        return n

    def covered_cells(self) -> List[GridCell]:
        out: List[GridCell] = []
        for key, count in sorted(self._hits.items()):
            la0, la1, lo0, lo1 = self._bounds(key)
            out.append(
                GridCell(
                    lat_i=key[0],
                    lon_i=key[1],
                    lat_min=la0,
                    lat_max=la1,
                    lon_min=lo0,
                    lon_max=lo1,
                    hit_count=count,
                )
            )
        return out

    def all_cells(self) -> List[GridCell]:
        """Every cell in the bbox (covered and empty)."""
        n_lat = max(1, math.ceil((self.lat_max - self.lat_min) / self.cell_deg))
        n_lon = max(1, math.ceil((self.lon_max - self.lon_min) / self.cell_deg))
        out: List[GridCell] = []
        for lat_i in range(n_lat):
            for lon_i in range(n_lon):
                key = (lat_i, lon_i)
                la0, la1, lo0, lo1 = self._bounds(key)
                out.append(
                    GridCell(
                        lat_i=lat_i,
                        lon_i=lon_i,
                        lat_min=la0,
                        lat_max=la1,
                        lon_min=lo0,
                        lon_max=lo1,
                        hit_count=self._hits.get(key, 0),
                    )
                )
        return out

    def gap_cells(self) -> List[GridCell]:
        return [c for c in self.all_cells() if not c.covered]

    def summary(self) -> dict:
        total = len(self.all_cells())
        covered = len(self._hits)
        gaps = total - covered
        return {
            "total_cells": total,
            "covered_cells": covered,
            "gap_cells": gaps,
            "coverage_ratio": (covered / total) if total else 0.0,
            "reason": (
                f"grid {self.cell_deg}° cells over "
                f"[{self.lat_min},{self.lat_max}]×[{self.lon_min},{self.lon_max}]: "
                f"{covered}/{total} covered, {gaps} gaps"
            ),
        }


def coverage_from_track(
    track: Sequence[Tuple[float, float]],
    *,
    cell_deg: float = 0.001,
    pad_deg: float = 0.0,
) -> CoverageGrid:
    """Build a grid from a GPS track; bbox is the track extent ± pad."""
    if not track:
        raise ValueError("track must contain at least one point")
    lats = [p[0] for p in track]
    lons = [p[1] for p in track]
    grid = CoverageGrid(
        lat_min=min(lats) - pad_deg,
        lat_max=max(lats) + pad_deg,
        lon_min=min(lons) - pad_deg,
        lon_max=max(lons) + pad_deg,
        cell_deg=cell_deg,
    )
    grid.mark_many(track)
    return grid
