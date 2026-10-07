"""Wi-Fi CSI sensing — the radio as a motion/presence sensor.

Because redux keeps **nexmon**, and nexmon has a CSI extension (`nexmon_csi`), the
Pi's own Wi-Fi radio can report per-subcarrier Channel State Information for the
frames it hears. The temporal variance of that CSI across a short window is a
motion signal: a still room is flat, a moving body perturbs the multipath. No
camera, no extra hardware.

This module is the *sensing math*, and it is pure and fully testable without a
radio. The honesty rules are the same ones the rest of redux lives by:

  - **No baseline → UNKNOWN, never "no motion."** A sensor that has not been
    calibrated against a quiet room does not get to claim the room is quiet. "We
    haven't measured the baseline" is not "nothing is there."
  - **Everything is relative to a measured baseline**, and the reading exposes the
    raw value, the baseline, and the z-score — glass-box, not a bare boolean.
  - **Nothing is invented.** Empty/malformed frames are skipped, not zero-filled.

The one hardware-coupled piece — parsing nexmon_csi's wire format — is fenced off
at the bottom and explicitly marked needs-hardware: the sensing engine operates on
abstract `CsiFrame`s, so a real UDP source and a synthetic test source are
interchangeable.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, List, Optional, Sequence, Tuple


class Sense(str, Enum):
    MOTION = "motion"
    STILL = "still"
    UNKNOWN = "unknown"      # not calibrated / not enough data — NOT "still"


class Occupancy(str, Enum):
    OCCUPIED = "occupied"
    VACANT = "vacant"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CsiFrame:
    """One CSI measurement: per-subcarrier amplitudes at a moment in time.

    `amp` is the magnitude per subcarrier (phase is kept optional and unused by the
    motion math, which is amplitude-based and far more robust on commodity radios)."""
    ts: float
    amp: Tuple[float, ...]
    rssi: Optional[int] = None
    channel: Optional[int] = None

    @classmethod
    def from_iq(cls, ts: float, iq: Sequence[Tuple[float, float]], **kw) -> "CsiFrame":
        amp = tuple(math.hypot(i, q) for i, q in iq)
        return cls(ts=ts, amp=amp, **kw)

    def valid(self) -> bool:
        return len(self.amp) > 0


@dataclass(frozen=True)
class SenseReading:
    sense: Sense
    value: float          # current motion value (mean per-subcarrier temporal variance)
    baseline: float       # calibrated quiet baseline (mean)
    z: float              # how many sigma above the quiet baseline
    reason: str
    ts: float = 0.0       # the observed frame's timestamp (so consumers have real time)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)   # population variance


def _motion_value(window: Sequence[CsiFrame]) -> Optional[float]:
    """The motion indicator for a window of frames: the mean, across subcarriers,
    of each subcarrier's temporal variance over the window. Higher = more change =
    more motion. None if the window is too short or frames disagree on width."""
    frames = [f for f in window if f.valid()]
    if len(frames) < 2:
        return None
    width = len(frames[0].amp)
    if width == 0 or any(len(f.amp) != width for f in frames):
        return None
    per_sub = []
    for j in range(width):
        per_sub.append(_variance([f.amp[j] for f in frames]))
    return _mean(per_sub)


@dataclass
class MotionSensor:
    """Sliding-window CSI motion detector with a measured quiet baseline."""
    window: int = 16
    sensitivity: float = 5.0          # z-score threshold above the quiet baseline
    _frames: Deque[CsiFrame] = field(default_factory=lambda: deque())
    _baseline_mean: Optional[float] = None
    _baseline_std: Optional[float] = None

    def __post_init__(self):
        self._frames = deque(maxlen=self.window)

    @property
    def calibrated(self) -> bool:
        return self._baseline_mean is not None

    def calibrate(self, quiet_frames: Sequence[CsiFrame]) -> dict:
        """Learn the quiet-room baseline by sliding the window over frames captured
        with nothing moving. Collects a motion value per full window and stores its
        mean+std. Raises if there isn't enough quiet data to be honest about it."""
        vals: List[float] = []
        win: Deque[CsiFrame] = deque(maxlen=self.window)
        for f in quiet_frames:
            if not f.valid():
                continue
            win.append(f)
            if len(win) == self.window:
                mv = _motion_value(win)
                if mv is not None:
                    vals.append(mv)
        if len(vals) < 2:
            raise ValueError(
                f"not enough quiet data to calibrate: need >={self.window + 1} valid "
                f"frames, got {len(vals)} window samples")
        self._baseline_mean = _mean(vals)
        self._baseline_std = math.sqrt(_variance(vals))
        return {"baseline_mean": self._baseline_mean, "baseline_std": self._baseline_std,
                "samples": len(vals)}

    def observe(self, frame: CsiFrame) -> SenseReading:
        """Feed one live frame; get a glass-box reading. UNKNOWN until the window is
        full AND a baseline has been measured — never a default 'still'."""
        if frame.valid():
            self._frames.append(frame)
        fts = float(getattr(frame, "ts", 0.0) or 0.0)
        if len(self._frames) < self.window:
            return SenseReading(Sense.UNKNOWN, 0.0, self._baseline_mean or 0.0, 0.0,
                                f"warming up ({len(self._frames)}/{self.window} frames)", ts=fts)
        if not self.calibrated:
            return SenseReading(Sense.UNKNOWN, _motion_value(self._frames) or 0.0, 0.0, 0.0,
                                "no quiet baseline measured yet — calibrate against a still room first", ts=fts)
        value = _motion_value(self._frames)
        if value is None:
            return SenseReading(Sense.UNKNOWN, 0.0, self._baseline_mean, 0.0,
                                "frames inconsistent (subcarrier width changed)", ts=fts)
        std = self._baseline_std or 0.0
        if std <= 1e-12:
            # degenerate near-flat baseline (synthetic/ideal): use a ratio, not a
            # fake sigma. z carries the ratio; the reason says so honestly.
            ratio = value / (abs(self._baseline_mean) + 1e-12)
            moving = ratio > 2.0
            reason = (f"{ratio:.1f}x quiet baseline (flat baseline, ratio test)" if moving
                      else "at/below quiet baseline (flat baseline, ratio test)")
            return SenseReading(Sense.MOTION if moving else Sense.STILL,
                                value, self._baseline_mean, ratio, reason, ts=fts)
        z = (value - self._baseline_mean) / std
        moving = z > self.sensitivity
        sense = Sense.MOTION if moving else Sense.STILL
        if moving:
            reason = (">1000sigma above quiet baseline (very still baseline)"
                      if z >= 1000 else f"{z:.1f}sigma above quiet baseline")
        else:
            reason = f"within {self.sensitivity:.0f}sigma of quiet baseline"
        return SenseReading(sense, value, self._baseline_mean, z, reason, ts=fts)


@dataclass
class OccupancyTracker:
    """Turns a stream of motion readings into occupied/vacant with hysteresis, so a
    single still moment in an occupied room doesn't flip it to vacant. 'Quiet
    presence' (a still person) is handled by the vacancy timeout: vacancy is only
    declared after a sustained motion-free stretch."""
    horizon: int = 40                 # readings kept for the occupied ratio
    occupy_ratio: float = 0.15        # motion in >=15% of recent readings → occupied
    vacant_after: int = 30            # consecutive still readings before declaring vacant
    _recent: Deque[bool] = field(default_factory=lambda: deque())
    _still_streak: int = 0
    _state: Occupancy = Occupancy.UNKNOWN

    def __post_init__(self):
        self._recent = deque(maxlen=self.horizon)

    def update(self, reading: SenseReading) -> Occupancy:
        if reading.sense is Sense.UNKNOWN:
            return self._state            # don't let UNKNOWN move the state either way
        moving = reading.sense is Sense.MOTION
        self._recent.append(moving)
        self._still_streak = 0 if moving else self._still_streak + 1
        ratio = (sum(1 for m in self._recent if m) / len(self._recent)) if self._recent else 0.0
        if ratio >= self.occupy_ratio:
            self._state = Occupancy.OCCUPIED
        elif self._still_streak >= self.vacant_after:
            self._state = Occupancy.VACANT
        return self._state

    @property
    def state(self) -> Occupancy:
        return self._state


def estimate_rate_hz(values: Sequence[float], sample_hz: float,
                     lo_hz: float = 0.1, hi_hz: float = 0.6) -> Optional[float]:
    """EXPERIMENTAL: estimate a dominant periodicity (e.g. breathing) in a motion
    series by autocorrelation, searching the [lo,hi] Hz band. Returns None when
    there isn't enough signal. Breathing-from-CSI is real in the literature but
    needs real-hardware validation before it's trusted — treated as experimental."""
    n = len(values)
    if n < 8 or sample_hz <= 0:
        return None
    m = _mean(values)
    x = [v - m for v in values]
    lo_lag = max(1, int(sample_hz / hi_hz))
    hi_lag = min(n - 1, int(sample_hz / lo_hz))
    if hi_lag <= lo_lag:
        return None
    best_lag, best = None, 0.0
    denom = sum(v * v for v in x) or 1e-9
    for lag in range(lo_lag, hi_lag + 1):
        ac = sum(x[i] * x[i - lag] for i in range(lag, n)) / denom
        if ac > best:
            best, best_lag = ac, lag
    if best_lag is None or best < 0.3:     # weak/no periodicity
        return None
    return sample_hz / best_lag


# --- hardware source + wire parser (FENCED: needs-hardware) ------------------ #

def parse_nexmon_csi(payload: bytes) -> Optional[CsiFrame]:
    """Parse one nexmon_csi UDP payload into a CsiFrame.

    ⚠️ NEEDS-HARDWARE: this models the documented nexmon_csi UDP layout (a fixed
    header followed by little-endian int16 I/Q pairs per subcarrier). The exact
    header length and subcarrier count are firmware/chip/bandwidth specific
    (bcm43455c0 on the Pi differs from other chips), so this MUST be validated
    against a real capture on the target firmware before it is trusted. The sensing
    math above does not depend on this function — it takes CsiFrames from any
    source, so this is the only piece that a real Pi has to confirm.
    """
    import struct
    HEADER = 18   # documented nexmon_csi header bytes (validate on hardware)
    if len(payload) <= HEADER or (len(payload) - HEADER) % 4 != 0:
        return None
    body = payload[HEADER:]
    n = len(body) // 4
    iq = []
    for k in range(n):
        i, q = struct.unpack_from("<hh", body, k * 4)
        iq.append((float(i), float(q)))
    if not iq:
        return None
    import time as _t
    return CsiFrame.from_iq(ts=_t.time(), iq=iq)


class CsiSource:
    """Protocol: anything that yields CsiFrames. The real source reads the
    nexmon_csi UDP socket; tests pass a synthetic/replay source. Duck-typed —
    implement `frames()` yielding CsiFrame."""
    def frames(self):   # pragma: no cover - interface
        raise NotImplementedError
