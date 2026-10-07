"""SenseEngine — one object that turns a CSI stream into occupancy state.

Wraps the MotionSensor (per-window motion) and the OccupancyTracker (hysteresis
occupied/vacant) behind a single `observe(frame) -> dict` and a glass-box
`status()`. Honest by construction: it reports UNKNOWN until it is both warmed up
and calibrated, and it never invents a reading.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .csi import (
    CsiFrame, MotionSensor, OccupancyTracker, SenseReading, Sense, Occupancy,
)


@dataclass
class SenseEngine:
    sensor: MotionSensor = field(default_factory=MotionSensor)
    occupancy: OccupancyTracker = field(default_factory=OccupancyTracker)
    _last: Optional[SenseReading] = None
    _observed: int = 0

    @classmethod
    def create(cls, *, window: int = 16, sensitivity: float = 5.0,
               vacant_after: int = 30) -> "SenseEngine":
        return cls(sensor=MotionSensor(window=window, sensitivity=sensitivity),
                   occupancy=OccupancyTracker(vacant_after=vacant_after))

    @property
    def calibrated(self) -> bool:
        return self.sensor.calibrated

    def calibrate(self, quiet_frames: Sequence[CsiFrame]) -> dict:
        return self.sensor.calibrate(quiet_frames)

    def observe(self, frame: CsiFrame) -> dict:
        r = self.sensor.observe(frame)
        occ = self.occupancy.update(r)
        self._last = r
        self._observed += 1
        return {
            "sense": r.sense.value,
            "occupancy": occ.value,
            "value": r.value,
            "baseline": r.baseline,
            "z": r.z,
            "reason": r.reason,
        }

    def status(self) -> dict:
        """Glass-box snapshot — honest about not-yet-measured state."""
        if self._last is None:
            return {"available": True, "calibrated": self.calibrated,
                    "sense": Sense.UNKNOWN.value, "occupancy": Occupancy.UNKNOWN.value,
                    "reason": "no CSI observed yet", "observed": 0}
        return {
            "available": True,
            "calibrated": self.calibrated,
            "sense": self._last.sense.value,
            "occupancy": self.occupancy.state.value,
            "value": self._last.value,
            "baseline": self._last.baseline,
            "z": self._last.z,
            "reason": self._last.reason,
            "observed": self._observed,
        }
