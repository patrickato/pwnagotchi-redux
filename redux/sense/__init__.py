"""redux.sense — Wi-Fi CSI sensing: the radio as a motion/presence sensor.

Because redux keeps nexmon, the Pi's own Wi-Fi can report per-subcarrier Channel
State Information; the temporal variance of that CSI is a motion signal. This
package is the pure, testable sensing math (MotionSensor, OccupancyTracker,
SenseEngine) plus a fenced, needs-hardware nexmon wire parser. No camera, no extra
hardware — and no fake green: UNKNOWN until calibrated, never a default "still".
"""
from .csi import (
    CsiFrame, MotionSensor, OccupancyTracker, SenseReading, Sense, Occupancy,
    estimate_rate_hz, parse_nexmon_csi, CsiSource,
)
from .engine import SenseEngine

__all__ = [
    "CsiFrame", "MotionSensor", "OccupancyTracker", "SenseReading",
    "Sense", "Occupancy", "estimate_rate_hz", "parse_nexmon_csi", "CsiSource",
    "SenseEngine",
]
