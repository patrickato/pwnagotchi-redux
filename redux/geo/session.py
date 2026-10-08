"""Survey session — start/stop with per-session stats."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Set

from redux.geo.db import Sighting, SightingStore


@dataclass
class SurveySession:
    name: str
    started_ts: float = field(default_factory=time.time)
    ended_ts: Optional[float] = None
    _macs: Set[str] = field(default_factory=set)
    _count: int = 0
    _active: bool = True

    def record(self, sighting: Sighting) -> None:
        if not self._active:
            return
        self._count += 1
        if sighting.mac:
            self._macs.add(f"{sighting.kind}:{sighting.mac}")

    def stop(self) -> None:
        self._active = False
        self.ended_ts = time.time()

    @property
    def active(self) -> bool:
        return self._active

    def stats(self) -> dict:
        end = self.ended_ts if self.ended_ts is not None else time.time()
        return {
            "name": self.name,
            "started_ts": self.started_ts,
            "ended_ts": self.ended_ts,
            "duration_s": end - self.started_ts,
            "observations": self._count,
            "unique_devices": len(self._macs),
            "active": self._active,
            "reason": (
                f"session '{self.name}': {self._count} obs, "
                f"{len(self._macs)} unique devices, "
                f"duration {end - self.started_ts:.1f}s"
            ),
        }


def run_session_into_store(
    session: SurveySession,
    store: SightingStore,
    sightings: List[Sighting],
) -> dict:
    """Insert sightings into store while recording session stats."""
    for s in sightings:
        store.insert(s)
        session.record(s)
    return session.stats()
